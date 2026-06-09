// Cloudflare Worker — reverse proxy for Binance Futures API.
// Lets a US-hosted server reach fapi.binance.com (which 451s US IPs directly).
//
// Deploy: dash.cloudflare.com → Workers & Pages → Create Worker → paste this →
// Deploy. Then on the server set:  BINANCE_BASE_URL=https://<name>.<sub>.workers.dev

const BINANCE_BASE = "https://fapi.binance.com";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const target = BINANCE_BASE + url.pathname + url.search;

    const upstream = await fetch(target, {
      method: request.method,
      headers: request.headers,
      body: request.method !== "GET" && request.method !== "HEAD"
        ? request.body
        : null,
    });

    const headers = new Headers(upstream.headers);
    headers.set("Access-Control-Allow-Origin", "*");

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers,
    });
  },
};
