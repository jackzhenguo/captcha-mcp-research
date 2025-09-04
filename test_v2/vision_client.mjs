#!/usr/bin/env node
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import axios from "axios";
import fs from "fs";

const SERVER_URL = "http://localhost:8931/sse";
const TARGET_URL = "http://127.0.0.1:8000/recaptcha";
const TRIALS = 100;
const WAIT_AFTER_NAV = 0.8;   // seconds
const WAIT_AFTER_PRESS = 1.8; // seconds

async function call(client, name, args = {}) {
  return client.callTool({ name, arguments: args });
}

function percentile(arr, p) {
  if (!arr.length) return 0;
  const a = [...arr].sort((x, y) => x - y);
  const idx = Math.min(a.length - 1, Math.ceil((p / 100) * a.length) - 1);
  return a[idx];
}

function stats(latencies) {
  const mean = Math.round(latencies.reduce((s, x) => s + x, 0) / latencies.length);
  return {
    mean,
    p50: percentile(latencies, 50),
    p90: percentile(latencies, 90),
    p95: percentile(latencies, 95),
    p99: percentile(latencies, 99),
  };
}

function timestamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

async function main() {
  const client = new Client({ name: "vision-mcp-client", version: "1.0.0" });
  await client.connect(new SSEClientTransport(new URL(SERVER_URL)));

  // start fresh
  try { await axios.post("http://127.0.0.1:8000/reset_metrics"); } catch {}

  let pass = 0, fail = 0;
  const latencies = [];
  const rows = [["trial","result","latency_ms","server_total","server_last"]];

  for (let i = 1; i <= TRIALS; i++) {
    const t0 = Date.now();
    try {
      await call(client, "browser_navigate", { url: TARGET_URL });
      await call(client, "browser_wait", { time: WAIT_AFTER_NAV });

      // Press 'v' (page listens for V/Enter to call grecaptcha.execute())
      await call(client, "browser_press_key", { key: "v" });

      // Give time for token + /api/verify roundtrip
      await call(client, "browser_wait", { time: WAIT_AFTER_PRESS });

      const { data } = await axios.get("http://127.0.0.1:8000/metrics", { timeout: 3000 });
      const ok = data?.last === "PASS";
      const ms = Date.now() - t0;

      rows.push([i, ok ? "PASS" : "FAIL", ms, data?.total ?? "", data?.last ?? ""]);
      latencies.push(ms);
      if (ok) pass++; else fail++;

      console.log(`[${i}/${TRIALS}] ${ok ? "PASS" : "FAIL"} in ${ms} ms — last=${data?.last} total=${data?.total}`);
    } catch (e) {
      fail++;
      const ms = Date.now() - t0;
      rows.push([i, "ERROR", ms, "", ""]);
      console.log(`[${i}/${TRIALS}] ERROR -> counted as FAIL: ${e.message}`);
    }
  }

  const s = stats(latencies);
  console.log(`\n=== Summary ===
Trials: ${TRIALS}
Pass  : ${pass}
Fail  : ${fail}
Latency (ms): mean=${s.mean}, p50=${s.p50}, p90=${s.p90}, p95=${s.p95}, p99=${s.p99}`);

  const fname = `results-${timestamp()}.csv`;
  const csv = rows.map(r => r.join(",")).join("\n");
  fs.writeFileSync(fname, csv);
  console.log(`Saved ${fname}`);
}

main().catch(e => { console.error(e); process.exit(1); });
