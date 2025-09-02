#!/usr/bin/env node
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import axios from "axios";
import fs from "fs";

const SERVER_URL = "http://localhost:8931/sse";        // Playwright MCP (started with --port 8931)
const TARGET_URL = "http://127.0.0.1:8000/recaptcha";  // Flask page
const TRIALS = 10;                                     // try 10 first while debugging
const DEBUG_DUMP = true;                               // set false after it works

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

/* ---------- snapshot parsing helpers ---------- */

function flattenTree(node, out) {
  if (!node || typeof node !== "object") return;
  // Normalize a node-like object to a record with role/name/ref/attributes
  const norm = {
    role: node.role || node.roleName || "",
    name: node.name || node.accessibleName || node.label || "",
    ref:  node.ref || node.reference || node.path || node.selector || null,
    attributes: node.attributes || node.props || node.properties || {},
    description: node.description || node.value || ""
  };
  out.push(norm);

  const kids = node.children || node.childNodes || node.items || node.nodes || [];
  if (Array.isArray(kids)) for (const k of kids) flattenTree(k, out);
}

function extractNodesFromBlock(block) {
  // 1) JSON block with nodes array
  if (block?.json?.nodes && Array.isArray(block.json.nodes)) {
    return block.json.nodes;
  }
  // 2) JSON block with tree/root/pages variants
  if (block?.json) {
    const j = block.json;
    const out = [];
    if (j.tree) flattenTree(j.tree, out);
    if (j.root) flattenTree(j.root, out);
    if (Array.isArray(j.pages)) {
      for (const p of j.pages) {
        if (p?.snapshot?.nodes) return p.snapshot.nodes;
        if (p?.snapshot?.tree) flattenTree(p.snapshot.tree, out);
        if (p?.snapshot?.root) flattenTree(p.snapshot.root, out);
      }
    }
    if (out.length) return out;
  }
  // 3) text block that actually contains JSON
  if (typeof block?.text === "string") {
    const t = block.text.trim();
    if (t.startsWith("{") || t.startsWith("[")) {
      try {
        const j = JSON.parse(t);
        if (Array.isArray(j.nodes)) return j.nodes;
        const out = [];
        if (j.tree) flattenTree(j.tree, out);
        if (j.root) flattenTree(j.root, out);
        if (Array.isArray(j.pages)) {
          for (const p of j.pages) {
            if (p?.snapshot?.nodes) return p.snapshot.nodes;
            if (p?.snapshot?.tree) flattenTree(p.snapshot.tree, out);
            if (p?.snapshot?.root) flattenTree(p.snapshot.root, out);
          }
        }
        if (out.length) return out;
      } catch {}
    }
  }
  return [];
}

function extractNodes(snapshot) {
  // Newer servers return content blocks
  if (Array.isArray(snapshot?.content)) {
    let all = [];
    for (const block of snapshot.content) {
      const nodes = extractNodesFromBlock(block);
      if (nodes.length) all = all.concat(nodes);
    }
    if (all.length) return all;
  }
  // Legacy shape
  if (Array.isArray(snapshot?.result?.nodes)) return snapshot.result.nodes;
  return [];
}

function findVerifyButton(nodes) {
  const isBtn = (n) => (n.role || "").toLowerCase().includes("button");
  const text = (s) => (s || "").toLowerCase();

  // Prefer exact accessible name
  let candidate = nodes.find(n => isBtn(n) && text(n.name) === "verify now");
  if (candidate) return candidate;

  // Fallbacks: contains “verify”
  candidate = nodes.find(n => isBtn(n) && text(n.name).includes("verify"));
  if (candidate) return candidate;

  // Check attributes/id/testid
  for (const n of nodes) {
    if (!isBtn(n)) continue;
    const attrs = n.attributes || {};
    const id = text(attrs.id || attrs["data-testid"]);
    if (id.includes("verify")) return n;
    const desc = text(n.description);
    if (desc.includes("verify")) return n;
  }
  return null;
}

/* ---------- MCP helpers ---------- */

async function callTool(client, name, args = {}) {
  return client.callTool({ name, arguments: args });
}

/* ---------- main ---------- */

async function runTrial(i, client) {
  const t0 = Date.now();

  await callTool(client, "browser_navigate", { url: TARGET_URL });

  // Give the page a moment to settle; then snapshot
  await callTool(client, "browser_wait", { time: 0.4 });
  let snap = await callTool(client, "browser_snapshot");

  if (DEBUG_DUMP && i === 1) {
    try { fs.writeFileSync("snap-1.json", JSON.stringify(snap, null, 2)); } catch {}
  }

  let nodes = extractNodes(snap);
  if (!nodes.length) {
    // Try once more after a tiny delay
    await callTool(client, "browser_wait", { time: 0.5 });
    snap = await callTool(client, "browser_snapshot");
    if (DEBUG_DUMP && i === 1) {
      try { fs.writeFileSync("snap-1b.json", JSON.stringify(snap, null, 2)); } catch {}
    }
    nodes = extractNodes(snap);
  }
  if (!nodes.length) throw new Error("snapshot nodes empty (could not parse any nodes from snapshot)");

  const btn = findVerifyButton(nodes);
  if (!btn?.ref) {
    // Log a small sample of what we saw
    const sample = nodes.slice(0, 20).map(n => `${n.role}:${n.name}`).join(" | ");
    throw new Error(`verify button not found; saw ${nodes.length} nodes. Sample: ${sample}`);
  }

  await callTool(client, "browser_click", { element: "Verify Now button", ref: btn.ref });
  await callTool(client, "browser_wait", { time: 1.5 });

  const snap2 = await callTool(client, "browser_snapshot");
  if (DEBUG_DUMP && i === 1) {
    try { fs.writeFileSync("snap-2.json", JSON.stringify(snap2, null, 2)); } catch {}
  }
  const nodes2 = extractNodes(snap2);

  const verdictNode = nodes2.find(n => {
    const role = (n.role || "").toLowerCase();
    const name = (n.name || "").toLowerCase();
    return (role === "status" || role.startsWith("heading")) &&
           (name.includes("pass: recaptcha") || name.includes("fail: recaptcha"));
  });

  const verdictText = (verdictNode?.name || "").toLowerCase();
  const ok = verdictText.includes("pass: recaptcha");

  console.log(`[${i}] ${ok ? "PASS" : "FAIL"} in ${Date.now() - t0} ms — nodes=${nodes.length}, verdict="${verdictNode?.name || "(none)"}"`);
  return ok;
}

async function main() {
  const client = new Client({ name: "local-mcp-client", version: "1.0.0" });
  await client.connect(new SSEClientTransport(new URL(SERVER_URL)));

  try { await axios.post("http://127.0.0.1:8000/reset_metrics"); } catch {}

  let pass = 0, fail = 0;
  const tStart = Date.now();

  for (let i = 1; i <= TRIALS; i++) {
    try {
      const ok = await runTrial(i, client);
      if (ok) pass++; else fail++;
    } catch (e) {
      fail++;
      console.log(`[${i}] ERROR -> counted as FAIL: ${e.message}`);
    }
  }

  console.log(`\n=== Summary ===
Trials: ${TRIALS}
Pass  : ${pass}
Fail  : ${fail}
Duration: ${Date.now() - tStart} ms`);

  if (DEBUG_DUMP) {
    console.log("Wrote snap-1.json / snap-1b.json / snap-2.json for the first trial (inspect these if matching still fails).");
  }
}

main().catch(e => { console.error(e); process.exit(1); });
