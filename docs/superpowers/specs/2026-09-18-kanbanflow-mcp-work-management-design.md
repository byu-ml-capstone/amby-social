# Kanban Flow MCP Work Management Design

## Goal

Expose the existing project, ticket, sprint, and membership API operations as
small, typed MCP tools so an MCP client can complete normal Kanban work without
falling back to the web UI or a generic HTTP tool.

## Approach

Add thin wrappers in `app/mcp_server.py`. Each tool calls the existing JSON API
through `_tool_request`; business rules, authorization, validation, and error
mapping remain in the API. No new API endpoints or dependencies are needed.

The MCP server will continue exposing explicit operations rather than a generic
request tool. This keeps available mutations discoverable and prevents callers
from selecting arbitrary API paths.

## Tools

### Projects

- `create_project(name)` (existing)
- `list_projects()`
- `get_project(slug)`
- `update_project(slug, name)`
- `delete_project(slug, confirm_slug)`

Project deletion remains owner-only and forwards `confirm_slug` as the API's
required `confirm` query parameter.

### Tickets

- `create_ticket(slug, title, description?, type?, priority?, story_points?, sprint_id?, assignee_id?)`
- `list_tickets(slug, status?, type?, priority?, sprint_id?, assignee_id?, cursor?, limit?)`
- `get_ticket(ticket_id)`
- `update_ticket(ticket_id, changes)`
- `update_ticket_status(ticket_id, status, resolution_notes?)`
- `delete_ticket(ticket_id)`

`changes` is a JSON object forwarded to the typed `TicketUpdate` API schema.
This preserves the difference between an omitted field and an explicit `null`,
which is required to clear an assignee, sprint, story points, or resolution
notes. The API rejects unknown fields and invalid values.

Ticket listing forwards only supplied filters. Its default and maximum limits
remain controlled by the API. Ticket deletion retains the API's owner-only and
sprint-history conflict rules.

### Sprints

- `create_sprint(...)` (existing)
- `list_sprints(slug)` (existing)
- `get_sprint(sprint_id)`
- `update_sprint(sprint_id, name?, goal?, start_date?, end_date?)`
- `start_sprint(sprint_id)` (existing)
- `close_sprint(sprint_id, next_sprint_id)` (existing)
- `get_sprint_history(sprint_id)`

Sprint update only edits planning sprints. Start and close remain separate
lifecycle operations. Sprint deletion is intentionally excluded because the API
does not support it and closed sprint history must remain intact.

### Project membership

- `list_project_members(slug)`
- `add_project_member(slug, email, role="MEMBER")`
- `update_project_member(slug, user_id, role)`
- `remove_project_member(slug, user_id)`

Membership mutations remain owner-only. The API continues preventing invalid
roles and removal of the last owner.

## Data and Errors

Path parameters use `_path_segment`. Query strings use the standard library's
URL encoding. Tools return the API's public response objects; existing concise
401, 403, 404, 409, and 422 errors remain unchanged. Delete tools return no
content on success.

## Testing

Keep tests in `tests/test_mcp_server.py`:

1. Assert the complete tool set and key required input fields.
2. Use the existing fake API request pattern to verify each wrapper's method,
   path, query, and JSON body.
3. Retain the existing path-safety and error-mapping tests.
4. After unit tests pass, reconnect the MCP client and smoke-test project,
   ticket, sprint, and membership reads plus reversible ticket mutations.

## Out of Scope

- Sprint deletion
- Comments and GitHub integration, which lack matching JSON API endpoints
- Token issuance or revocation through MCP
- A generic arbitrary-path HTTP tool
- New API business logic or authorization rules
