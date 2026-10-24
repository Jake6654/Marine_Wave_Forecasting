import "server-only";
import { forwardCollection } from "../../../../lib/manual-collection";

export const runtime = "nodejs";

export function POST(request: Request) {
  return forwardCollection(request, {
    baseUrl: process.env.MARINE_API_BASE_URL ?? "http://127.0.0.1:8000",
    token: process.env.MARINE_COLLECTION_TOKEN,
  });
}
