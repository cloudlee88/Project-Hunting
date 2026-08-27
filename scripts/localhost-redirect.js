const http = require("http");

const targetPort = process.env.TARGET_PORT || "3001";
const listenPort = Number(process.env.LISTEN_PORT || "80");

const server = http.createServer((req, res) => {
  const path = req.url || "/";
  const location = `http://localhost:${targetPort}${path}`;
  res.writeHead(302, {
    Location: location,
    "Content-Type": "text/plain; charset=utf-8",
  });
  res.end(`Redirecting to ${location}\n`);
});

server.listen(listenPort, "127.0.0.1", () => {
  console.log(`localhost redirect listening on http://localhost:${listenPort}`);
});

server.on("error", (error) => {
  console.error(error.message || error);
  process.exit(1);
});
