import "server-only";

import { timingSafeEqual } from "node:crypto";

export function secretsMatch(supplied: string | null, expected: string) {
  if (!supplied) return false;
  const suppliedBuffer = Buffer.from(supplied);
  const expectedBuffer = Buffer.from(expected);
  return suppliedBuffer.length === expectedBuffer.length
    && timingSafeEqual(suppliedBuffer, expectedBuffer);
}

export function cronAuthorized(authorization: string | null, cronSecret: string) {
  return secretsMatch(authorization, `Bearer ${cronSecret}`);
}
