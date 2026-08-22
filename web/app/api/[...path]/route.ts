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

  const init: RequestInit = {
    method: request.method,
    headers: { "Content-Type": request.headers.get("content-type") || "application/json" },
    cache: "no-store",
  };
  if (!["GET", "HEAD"].includes(request.method)) {
    init.body = await request.text();
  }

  try {
    const upstream = await fetch(target, init);
    const headers = new Headers();
    const contentType = upstream.headers.get("content-type");
    if (contentType) headers.set("content-type", contentType);
    // Page images are content-addressed by document hash and never change.
    if (contentType?.startsWith("image/")) {
      headers.set("cache-control", "public, max-age=31536000, immutable");
    }
    return new Response(upstream.body, { status: upstream.status, headers });
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
