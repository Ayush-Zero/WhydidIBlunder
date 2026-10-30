interface JsonResponseInit {
  status?: number
  headers?: Record<string, string>
}

export function jsonResponse(
  body: unknown,
  { status = 200, headers = {} }: JsonResponseInit = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
  })
}
