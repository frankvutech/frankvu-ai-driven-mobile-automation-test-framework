# API testing

API plans (`platforms: [api]`) run against the backend without any simulator or emulator. API steps
can also appear in UI plans, and API-backed fixtures and questions let one run correlate UI actions
with backend state (the same actor, the same token).

## Contract first

Every API step names an operation from `contracts/openapi/backend.yaml` by method and path template.
The validator rejects unknown operations, undeclared statuses, missing or unknown path parameters,
undeclared query parameters, bodies on operations that take none, and success expectations that the
contract makes impossible (an authenticated operation without an actor; an admin operation with a
customer). At run time, the response body is validated against the schema the contract declares for
the returned status (`expect.schema`, default `true`); a mismatch is an `api_contract_violation`.

`mtf contracts check-openapi --base-url <backend>` compares the committed contract with the spec
the running backend serves (the API CI job runs it before the plans).

## Authentication

Actors reference accounts in `contracts/test-data.yaml`. The first request that needs an actor's token
logs in once (`POST /api/v1/auth/login`, recorded as a `setup` exchange), caches the token for the run,
and registers both the password and the token with the redactor. `token: none` sends no token and
`token: invalid` sends a syntactically valid, unsigned token for negative tests. After
`reset_backend_data` cached tokens are discarded.

## Setup APIs vs. product APIs

Operations tagged `Test support` in the contract (`POST /api/v1/test/reset`,
`GET /api/v1/test/server-error`) are setup/diagnostic endpoints. They are allowed only when the
selected environment sets `backend.test_support: true` (local and CI); shared environments keep it
off, and the validator then rejects plans that use them. Exchanges record `test_support: true`.
Fixtures (`reset_backend_data`, `seed_cart`, `clear_cart`) are the preferred way to prepare data; the
seed and cart fixtures use the product API, so setup works the same as a real client.

## Determinism

Every API plan starts with `reset_backend_data`, so plans pass in any order (verified by running
`plans/api` forwards and in reverse). The seed is fixed: 20 products with known prices and stock
(`product_019` has 1 unit, `product_020` is sold out), four accounts, `qa` starts with `product_002`
in the wishlist, and the first order after a reset is `ORDER-0001`.

## Coverage (`plans/api/`)

| Plan | Covers |
|---|---|
| `health.yaml` | `/health`, `/ready` |
| `auth_login.yaml` | login success, wrong password, unknown user, locked account, empty body (validation), malformed JSON, `/auth/me` |
| `auth_authorization.yaml` | no token (401 `UNAUTHORIZED`), invalid token (401 `INVALID_TOKEN`), customer forbidden from admin (403), admin allowed, logout revokes the token |
| `products.yaml` | listing, detail, 404, search, empty search, category filter, price sorting, combined filter/sort/page, last partial page, page past the end, invalid size (400), categories |
| `cart.yaml` | empty cart, add, add again accumulates, update quantity, quantity above limit (400), remove, remove missing (404), sold-out conflict (409), unknown product (404), clear |
| `wishlist.yaml` | seeded wishlist, add, duplicate (409), remove, remove missing (404) |
| `checkout_orders.yaml` | empty-cart checkout (409), seeded cart via fixtures, checkout `ORDER-0001` and total, cart emptied, stock decremented, order list and detail, another user's order (404), admin ships the order |
| `validation_and_errors.yaml` | register validation details (400), duplicate email (409), invalid order ID (400), deterministic server error (500, no internals leaked) |

## Captured evidence

Each exchange is stored in `result.json` (and shown in the HTML report) with method, URL, headers,
body, status, response headers and body, duration, the step it belongs to and its purpose
(`test`/`setup`). `Authorization`, cookies, and JSON fields such as `password`, `token`, `secret`,
`apiKey` are replaced by `«redacted»`, as is any known secret value or JWT-shaped string anywhere.

## Run

```bash
uv run mtf run plans/api --platform api --env local          # backend on localhost:8080
make docker-test-api                                          # prebuilt backend JAR + MTF in Compose
```
