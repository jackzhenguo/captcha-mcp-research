
import requests, sseclient, json, itertools, threading, urllib.parse

class MCPClient:
    def __init__(self, root="http://localhost:8931/sse"):
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json, text/event-stream"})
        self.lock    = threading.Lock()
        self.ids     = itertools.count(1)

        r = self.session.get(root, stream=True)
        sse = sseclient.SSEClient(r)
        for e in sse.events():
            if e.event == "endpoint":
                self.session_url = urllib.parse.urljoin(root, e.data)
                break
        self.sse_resp = self.session.get(self.session_url, stream=True)
        self.sse      = sseclient.SSEClient(self.sse_resp)
        self.post_url = self.session_url.replace("/sse", "/mcp")

    def call(self, tool, args=None):
        args = args or {}
        call_id = next(self.ids)
        payload = {"id": call_id, "tool": tool, "input": args}

        print(f"→ posting to {self.post_url}")
        print(f"→ payload: {payload}")

        with self.lock:
            self.session.post(self.post_url, json=payload,
                headers={"Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json"})
            for e in self.sse.events():
                if e.event == "tool_result":
                    data = json.loads(e.data)
                    if data.get("id") == call_id:
                        return data.get("output")
