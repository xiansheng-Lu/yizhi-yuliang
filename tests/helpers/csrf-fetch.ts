import { ApiClient } from "../../src/services/api-client";

/** Domain fixtures keep their own response contract; CSRF is tested separately. */
export function withCsrf(fetcher: typeof fetch): typeof fetch {
  return async (input, options) => {
    if (new URL(String(input)).pathname === "/v1/auth/csrf") {
      return Response.json({
        data: { token: "domain-test-csrf", expiresAt: new Date(Date.now() + 600000).toISOString() },
        requestId: "domain-test-csrf",
        schemaVersion: "command-job-v1.0",
      });
    }
    return fetcher(input, options);
  };
}

export class CsrfApiClient extends ApiClient {
  constructor(baseUrl: string, fetcher: typeof fetch = fetch) {
    super(baseUrl, withCsrf(fetcher));
  }
}
