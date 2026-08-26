/**
 * Runtime proxy to the pipeline API.
 *
 * Next evaluates `rewrites()` at build time and bakes the target into the route
 * manifest, so a rewrite cannot pick up API_BASE from the container environment.
 * A route handler reads it per request instead, which means the same image works
 * whether the app is running under `npm run dev` or in Docker.
 *
 * Keeping browser traffic same-origin also means the API never has to be exposed
 * to the browser directly, and no CORS configuration is load-bearing.
 */

const API_BASE = () => process.env.API_BASE || "http://localhost:8001";

async function proxy(request: Request, path: string[]) {
  const url = new URL(request.url);
  const target = `${API_BASE()}/api/${path.join("/")}${url.search}`;

  // Starlette does not auto-route HEAD, so a HEAD forwarded as-is would 405
  // even where GET works. Fetch with GET and return the headers without a body,
  // which is what HEAD is supposed to mean.
  const isHead = request.method === "HEAD";
  const init: RequestInit & { duplex?: "half" } = {
    method: isHead ? "GET" : request.method,
    headers: { "Content-Type": request.headers.get("content-type") || "application/json" },
    cache: "no-store",
  };
  if (!["GET", "HEAD"].includes(request.method)) {
    // Stream the body through byte for byte. Reading it as text decodes it as
    // UTF-8, so every byte that is not valid UTF-8 becomes U+FFFD and the
    // re-encoded result is garbage -- which destroys a JPEG outright and leaves
    // a PDF structurally intact but with its content streams shredded. Uploads
    // are the only binary traffic here, and that is exactly what it broke.
    init.body = request.body;
    init.duplex = "half"; // required when a stream is used as a request body
  }

  try {
    const upstream = await fetch(target, init);
    const headers = new Headers();
    const contentType = upstream.headers.get("content-type");
    if (contentType) headers.set("content-type", contentType);
    // Page images must revalidate, not be cached hard. The URL is keyed by
    // invoice id, not by content hash, and ids restart at 1 after a reset -- so
    // an "immutable" cache would happily show the previous invoice_01 in place
    // of the current one. The upstream FileResponse sends an ETag, so
    // revalidation costs a 304 and returns the right image every time.
    if (contentType?.startsWith("image/")) {
      headers.set("cache-control", "no-cache");
      const etag = upstream.headers.get("etag");
      if (etag) headers.set("etag", etag);
    }
    return new Response(isHead ? null : upstream.body, {
      status: upstream.status,
      headers,
    });
  } catch (error) {
    return Response.json(
      { detail: `cannot reach the pipeline API at ${API_BASE()}: ${error}` },
      { status: 502 },
    );
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(request: Request, ctx: Ctx) {
  return proxy(request, (await ctx.params).path);
}
// Browsers and caches issue HEAD for images; without it they get a 405.
export async function HEAD(request: Request, ctx: Ctx) {
  return proxy(request, (await ctx.params).path);
}
export async function POST(request: Request, ctx: Ctx) {
  return proxy(request, (await ctx.params).path);
}
export async function PATCH(request: Request, ctx: Ctx) {
  return proxy(request, (await ctx.params).path);
}
export async function DELETE(request: Request, ctx: Ctx) {
  return proxy(request, (await ctx.params).path);
}

export const dynamic = "force-dynamic";
