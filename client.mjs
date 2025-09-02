#!/usr/bin/env node
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import axios from "axios";

const SERVER_URL = "http://localhost:8931/sse";      // Playwright MCP (started with --port 8931)
const TARGET_URL = "http://127.0.0.1:8000/recaptcha"; // Your Flask page
const TRIALS = 100;

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function callTool(client, name, args = {}) {
  // High-level helper (equivalent to tools/call)
  return client.callTool({ name, arguments: args });
}

async function main() {
  // Connect to Playwright MCP via SSE
  const client = new Client({ name: "local-mcp-client", version: "1.0.0" });
  await client.connect(new SSEClientTransport(new URL(SERVER_URL)));

  // Optional: reset Flask metrics
  try { await axios.post("http://127.0.0.1:8000/reset_metrics"); } catch {}

  let pass = 0, fail = 0;
  const tStart = Date.now();

  for (let i = 1; i <= TRIALS; i++) {
    const t0 = Date.now();
    try {
      await callTool(client, "browser_navigate", { url: TARGET_URL });

      // Snapshot: find the host-page "Verify Now" button
      const snap1 = await callTool(client, "browser_snapshot");
      const nodes = snap1?.content?.[0]?.type === "json"
        ? snap1.content[0].json?.nodes ?? []
        : snap1?.result?.nodes ?? []; // fallback for older servers

      const btn = nodes.find(n => {
        const role = (n.role || "").toLowerCase();
        const name = (n.name || "").toLowerCase();
        return role === "button" && (name.includes("verify now") || name.includes("verify recaptcha"));
      });
      if (!btn?.ref) throw new Error("verify button not found in snapshot");

      // Click it (snapshot mode)
      await callTool(client, "browser_click", { element: "Verify Now button", ref: btn.ref });

      // Small wait for token + server verify
      await callTool(client, "browser_wait", { time: 1.5 });

      // Read verdict from host page
      const snap2 = await callTool(client, "browser_snapshot");
      const nodes2 = snap2?.content?.[0]?.type === "json"
        ? snap2.content[0].json?.nodes ?? []
        : snap2?.result?.nodes ?? [];
      const verdictNode = nodes2.find(n => {
        const role = (n.role || "").toLowerCase();
        const name = (n.name || "").toLowerCase();
        return (role === "status" || role.startsWith("heading")) &&
               (name.includes("pass: recaptcha") || name.includes("fail: recaptcha"));
      });

      const verdictText = (verdictNode?.name || "").toLowerCase();
      const ok = verdictText.includes("pass: recaptcha");
      if (ok) pass++; else fail++;

      console.log(`[${i}/${TRIALS}] ${ok ? "PASS" : "FAIL"} in ${Date.now() - t0} ms — verdict="${verdictNode?.name || "(none)"}"`);
    } catch (e) {
      fail++;
      console.log(`[${i}/${TRIALS}] ERROR -> counted as FAIL: ${e.message}`);
    }
  }

  console.log(`\n=== Summary ===
Trials: ${TRIALS}
Pass  : ${pass}
Fail  : ${fail}
Duration: ${Date.now() - tStart} ms`);
}

main().catch(e => { console.error(e); process.exit(1); });
