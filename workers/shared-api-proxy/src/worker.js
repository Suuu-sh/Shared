import { ROUTES } from "./routes.js";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

function stripHopByHopHeaders(headers) {
  const next = new Headers(headers);
  for (const header of HOP_BY_HOP_HEADERS) {
    next.delete(header);
  }
  return next;
}

function buildProxyRequest(request, route, incomingUrl, env = {}) {
  const origin = new URL(route.origin);
  const targetUrl = new URL(incomingUrl.pathname + incomingUrl.search, origin);
  const headers = stripHopByHopHeaders(request.headers);

  headers.set("Host", origin.host);
  headers.set("X-Forwarded-Host", incomingUrl.host);
  headers.set("X-Forwarded-Proto", incomingUrl.protocol.replace(":", ""));
  headers.set("X-Proxy-Route", route.name);

  const originVerifySecret = route.originVerifySecretEnv
    ? env[route.originVerifySecretEnv]
    : undefined;
  if (originVerifySecret) {
    headers.set("X-Origin-Verify", originVerifySecret);
  }

  return new Request(targetUrl.toString(), {
    method: request.method,
    headers,
    body: request.body,
    redirect: "manual",
  });
}

function notFound(hostname) {
  return new Response(
    JSON.stringify({ ok: false, error: "proxy_route_not_found", hostname }),
    {
      status: 404,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    },
  );
}

export default {
  async fetch(request, env) {
    const incomingUrl = new URL(request.url);
    const route = ROUTES[incomingUrl.hostname.toLowerCase()];

    if (!route) {
      return notFound(incomingUrl.hostname);
    }

    const proxyRequest = buildProxyRequest(request, route, incomingUrl, env);
    const response = await fetch(proxyRequest);
    const headers = stripHopByHopHeaders(response.headers);
    headers.set("X-Shared-Api-Proxy", route.name);

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
