# Audit schema & đa-tenant (multi-tenant) — neurix_enterprise

**Phạm vi:** chỉ đọc (read-only). Không có migration nào được chạy, không có thay đổi file nào, không có kết nối tới DB thực tế được thực hiện trong quá trình audit này.

**Ghi chú quan trọng phát hiện ngay từ đầu:** repo `neurix_enterprise` triển khai tenant theo mô hình **một DB Cloud SQL + một Slack App riêng cho mỗi tenant** (silo theo hạ tầng — xem `deploy/TENANT_RUNBOOK.md` và mục 9 dưới đây), **không phải** mô hình "một DB chung, lọc bằng `tenant_id`" như thiết kế mục tiêu nêu trong yêu cầu audit. Trong phạm vi *một* DB tenant, khái niệm "nhiều team trong một Slack workspace" đã tồn tại sẵn dưới tên **`orgs` / `org_members`** (được thêm ở migration `0002`), cùng cơ chế chia sẻ `share_node`. Toàn bộ audit dưới đây mô tả đúng mô hình **hiện tại** (silo theo tenant ở tầng hạ tầng + `orgs` là "team" trong một workspace), để làm nền cho việc thiết kế lại theo hướng multi-tenant dùng chung DB mà yêu cầu đề ra.

---

## 1. Kiểm kê schema đầy đủ

Nguồn: `backend/db/migrations/0001_init_from_neurix.sql` → `0005_org_slack_workspace.sql` (5 file, tổng 681 dòng). Không có file SQL nào khác ngoài các migration này (không có `sql/*.sql` rời).

### 1.1 Extension

✅ `CREATE EXTENSION IF NOT EXISTS vector;` và `unaccent;` — `0001:11-12`.

### 1.2 Bảng `public.node_pool` — `0001:16-35`
| Cột | Kiểu | Null | Default |
|---|---|---|---|
| fingerprint | text | ✅ | — |
| origin | text | ✅ | `'web'` |
| id | uuid | ❌ | `gen_random_uuid()` |
| created_at / updated_at | timestamptz | ✅ | `now()` |
| user_id | uuid | ❌ | — |
| title, ai_title, source | text | ✅ | — |
| size_tokens | integer | ✅ | — |
| summary | text | ✅ | — |
| metadata | jsonb | ✅ | — |
| summary_vec | `vector(768)` | ✅ | — |
| text_search_vec | tsvector **GENERATED ALWAYS AS** `to_tsvector('english', coalesce(summary,''))` STORED | ✅ | — |
| client_node_id | uuid | ✅ | — |
| status | text | ❌ | `'DRAFT'`, CHECK ∈ {DRAFT, PROCESSING, READY, FAILED} |
| raw_content | text | ✅ | — |
| **PK** | id | | |

❌ **Không có `org_id`, không có `origin_node_id`/`origin_user_id`** trên `node_pool` — migration `0002` nói rõ: *"Nothing is added to node_pool (no org_id, no origin_node_id, no origin_user_id, no processing_status...)"* (`0002:3-5`).

Index: `idx_node_user (user_id)` `0001:254`; `idx_node_pool_origin (origin)` `0001:255`; `idx_node_vec` **ivfflat** `(summary_vec vector_cosine_ops) WITH (lists='100')` `0001:256`; `idx_node_pool_fingerprint (fingerprint)` `0001:257`; UNIQUE `node_pool_user_client_node_id_uq (user_id, client_node_id) WHERE client_node_id IS NOT NULL` `0001:258`; `node_pool_user_status_created_at_idx (user_id, status, created_at DESC)` `0001:259`.

### 1.3 Bảng `public.key_pool` — `0001:37-44`, mở rộng `0002:81-95`
| Cột | Kiểu | Null | Default |
|---|---|---|---|
| user_id | uuid | ✅ (đã `DROP NOT NULL` ở `0002:83`) | — |
| key_text | text | ❌ | — |
| id | uuid | ❌ | `gen_random_uuid()` |
| created_at | timestamptz | ✅ | `now()` |
| key_vec | `vector(768)` | ✅ | — |
| org_id | uuid | ✅ (thêm `0002:84`) | — |

Constraint: PK `id`; **CHECK XOR** `key_pool_scope_xor: (user_id IS NOT NULL) <> (org_id IS NOT NULL)` — `0002:88-91` (được thêm **có điều kiện tồn tại**, guard bằng tra `pg_constraint`). UNIQUE `key_pool_user_id_key_text_key (user_id, key_text)` `0001:260`; UNIQUE `key_pool_org_id_key_text_key (org_id, key_text) WHERE org_id IS NOT NULL` `0002:94-95`.
Index: `idx_key_user (user_id)` `0001:261`; `idx_key_vec` **ivfflat** `(key_vec vector_cosine_ops) WITH (lists='100')` `0001:262`.

### 1.4 Bảng `public.node_keys` — `0001:46-53`
`node_id uuid NOT NULL`, `key_id uuid NOT NULL`, `weight real DEFAULT 1.0`. PK composite `(node_id, key_id)`. FK → `node_pool(id)`, FK → `key_pool(id)`.

### 1.5 Bảng `public.share_node` — `0001:58-72`, mở rộng `0002:46-79`, index `0004:11-24`
| Cột | Kiểu | Null | Default |
|---|---|---|---|
| owner_user_id | uuid | ❌ | — |
| shared_user_id | uuid | ✅ (đã `DROP NOT NULL` ở `0002:49`) | — |
| node_id | uuid | ❌ | — |
| expires_at | timestamptz | ✅ | — |
| message | text | ✅ | — |
| id | uuid | ❌ | `gen_random_uuid()` |
| permission | text | ❌ | `'view'`, CHECK ∈ {view, comment, edit} |
| is_active | boolean | ❌ | `true` |
| metadata | jsonb | ✅ | `'{}'` |
| created_at / updated_at | timestamptz | ❌ | `now()` |
| org_id | uuid | ✅ (thêm `0002:48`) | — |
| status | text | ❌ (thêm `0002:77-78`) | `'active'`, CHECK ∈ {pending, active, declined} |
| can_view_raw | boolean | ❌ (thêm `0002:79`) | `false` |

Constraint: PK `id`; FK `node_id → node_pool(id)`; FK `org_id → orgs(id)`; **CHECK XOR** `share_node_target_xor: (shared_user_id IS NOT NULL) <> (org_id IS NOT NULL)` `0002:53-57`.
Index: UNIQUE `share_node_person_open_uq (node_id, shared_user_id) WHERE shared_user_id IS NOT NULL AND status IN ('pending','active')` `0004:14-16`; UNIQUE `share_node_org_uq (node_id, org_id) WHERE org_id IS NOT NULL` `0004:19-21`; `idx_share_node_recipient` `0004:23`; `idx_share_node_org` `0004:24`.

### 1.6 Bảng `public.connection` — `0001:74-89`, mở rộng `0002:97-110`
| Cột | Kiểu | Null | Default |
|---|---|---|---|
| user_id | uuid | ✅ (đã `DROP NOT NULL` `0002:98`) | — |
| source_node, target_node | uuid | ✅ | — |
| score | real | ❌ | — |
| via_key_id | uuid | ✅ | — |
| id | uuid | ❌ | `gen_random_uuid()` |
| created_at | timestamptz | ✅ | `now()` |
| share_node_id | uuid | ✅ | — |
| is_share | boolean | ❌ | `false` |
| org_id | uuid | ✅ (thêm `0002:99`) | — |

Constraint: PK `id`; FK `source_node/target_node → node_pool(id)`; FK `share_node_id → share_node(id)`; FK `via_key_id → key_pool(id)`; **CHECK XOR** `connection_scope_xor` `0002:103-106`.
Index: `idx_conn_src (user_id, source_node)`, `idx_conn_tgt (user_id, target_node)` `0001:252-253`; `idx_conn_org_src/tgt (org_id, ...) WHERE org_id IS NOT NULL` `0002:109-110`; `idx_conn_share_node (share_node_id) WHERE share_node_id IS NOT NULL` `0004:25`.

### 1.7 Bảng `public.orgs` — `0002:21-26`, mở rộng `0005:22-23`
`id uuid PK DEFAULT gen_random_uuid()`, `name text NOT NULL`, `created_by uuid NOT NULL`, `created_at timestamptz NOT NULL DEFAULT now()`, `slack_team_id text` (thêm `0005:22`), `slack_workspace_url text` (thêm `0005:23`). Không FK, không UNIQUE trên `name`.

### 1.8 Bảng `public.org_members` — `0002:28-35`, index `0004:26`
`org_id uuid NOT NULL REFERENCES orgs(id)`, `uid uuid NOT NULL` (ghi chú code: *"Firebase uid, no FK (loose coupling from legacy tables)"* — `0002:30`), `role text NOT NULL CHECK (role IN ('admin','member'))`, `display_name text`, `joined_at timestamptz NOT NULL DEFAULT now()`. PK composite `(org_id, uid)`. Index `idx_org_members_uid (uid)` `0004:26`.

### 1.9 Bảng `public.retrieval_log` — `0002:37-44`
`id uuid PK`, `user_id uuid` (nullable), `org_id uuid` (nullable), `question text NOT NULL`, `used_node_ids uuid[] NOT NULL DEFAULT '{}'`, `created_at timestamptz NOT NULL DEFAULT now()`. ❌ Không có CHECK XOR giữa `user_id`/`org_id` (khác với `key_pool`/`connection`/`share_node`) — có thể cả hai cùng NULL hoặc cùng có giá trị, không bị chặn ở DB.

### 1.10 Bảng `public.notifications` — `0001:130-141`, mở rộng `0002:112-114`, index `0004:27`
`id uuid PK` (❌ không có DEFAULT — code phải tự `gen_random_uuid()`, xem `repositories/notifications.py:34-37`), `user_id uuid NOT NULL`, `share_node_id uuid` FK → `share_node(id)`, `message text NOT NULL`, `read_at timestamptz`, `type varchar NOT NULL DEFAULT 'share'`, `is_read boolean NOT NULL DEFAULT false`, `created_at timestamptz NOT NULL DEFAULT now()`, `org_id uuid` (thêm `0002:113`), `payload jsonb NOT NULL DEFAULT '{}'` (thêm `0002:114`). Index `idx_notifications_unread (user_id) WHERE is_read=false` `0004:27`.

### 1.11 Bảng `public.node_drafts` — `0001:194-206`
`user_id uuid NOT NULL`, `client_node_id uuid NOT NULL`, `raw_content text NOT NULL`, `source text`, `preview jsonb`, `error text`, `id uuid PK DEFAULT gen_random_uuid()`, `status text NOT NULL DEFAULT 'DRAFT'`, `created_at/updated_at timestamptz DEFAULT now()`. Không UNIQUE/FK nào khác.

### 1.12 Bảng `auth.users` (shim) — `0001:144-149`
Schema `auth` riêng, `id uuid PK`, `email text`, `created_at timestamptz NOT NULL DEFAULT now()`. Dùng làm "bảng người dùng" thay cho Supabase.

### 1.13 Các bảng khác (giữ nguyên từ `neurix`, không liên quan trực tiếp đa-tenant)
`checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` (LangGraph checkpointer — `0001:91-128`); `ai_message_feedback`, `survey_response`, `surveys` (`0001:151-192`, FK → `auth.users`); `telegram_link_tokens`, `telegram_sessions` (`0001:208-225`, không dùng trong code backend hiện tại theo kiểm tra ở mục 6); `user_journey` (`0001:227-236`); `mcp_api_keys` (`0001:238-248`, FK → `auth.users`).

### 1.14 Hàm/View SQL

❌ **Không có view nào.** Chỉ có một hàm:

✅ `public.retrieve_candidate_nodes` — **2 overload cùng tồn tại** (Postgres cho phép do khác số tham số):
- Bản 6 tham số (`0001:267-271`): `(user_id uuid, query_text text, query_embedding vector, scope text DEFAULT 'merged', match_count integer DEFAULT 30, query_language text DEFAULT 'en')`.
- Bản 7 tham số (`0003:16-20`): thêm `org_id uuid DEFAULT NULL`, mọi phần khác giống byte-for-byte bản 6 tham số, chỉ CTE `shared_nodes` được mở rộng để union thêm điều kiện `sn.org_id = org_id` (`0003:28-30`).

Logic hàm (giống nhau ở cả 2 bản, mô tả theo bản 7 tham số): 4 CTE — `shared_nodes` (node được share active & chưa hết hạn, theo `shared_user_id` hoặc `org_id`), `scoped_nodes` (áp theo `scope` ∈ owned/shared/merged), `semantic_candidates` (cosine trên `summary_vec`), `lexical_candidates` (full-text, có nhánh riêng cho `query_language='vi'` dùng `unaccent`+`simple` dictionary), `key_candidates` (cosine trên `key_vec` qua `node_keys`). Kết quả hợp nhất bằng `UNION ALL` + `DISTINCT ON (id)`, giới hạn `LEAST(match_count, 200)`.

Ghi chú trong chính file migration (`0001:381-386`): các hàm cũ của `neurix` (`hybrid_search_node_pool`, `hybrid_search_sample`, `full_text_search_node_pool`, các hàm `storage.*`) **chủ đích không port sang**.

---

## 2. Mô hình scoping hiện tại theo từng bảng

| Bảng | Scope hiện tại | Bằng chứng |
|---|---|---|
| `node_pool` | **Chỉ `user_id`** — không có `org_id`. Mọi quyền truy cập "team" đều đi qua bảng `share_node` (tham chiếu), không có cột org trên `node_pool` bản thân. | `repositories/nodes.py:79-129` (`list_nodes`, `recent_ready_nodes`, `search_similar_nodes` đều lọc `WHERE user_id = :user_id`) |
| `key_pool` | **XOR `user_id` / `org_id`** ở DB (`0002:88-91`), nhưng ❌ **`org_id` không được code ứng dụng dùng ở bất kỳ đâu** — xem mục dưới. | `repositories/keys.py` toàn bộ hàm (`get_or_create_key`, `existing_key_texts`, `search_similar_key`, `nodes_sharing_key`) chỉ nhận `user_id`, không có tham số `org_id` nào |
| `connections` (`connection`) | **Cả hai**: ghi cá nhân dùng `user_id` (is_share=false) — `repositories/connections.py:21-30`; ghi từ share dùng `user_id` (share cá nhân) HOẶC `org_id` (share team) — `repositories/connections.py:86-106`, do `services/share.py:56-68` quyết định theo `share["shared_user_id"] is not None` | |
| `share_node` | `owner_user_id` luôn có; target là XOR `shared_user_id`/`org_id` (CHECK `share_node_target_xor`, `0002:53-57`) | `repositories/shares.py:66-74` (person), `:132-140` (team) |
| `node_drafts` | **Chỉ `user_id`** | `repositories/drafts.py:30-47` |
| `notifications` | `user_id` luôn có (người nhận); `org_id` chỉ là metadata bổ sung (không dùng để lọc quyền xem — notification luôn thuộc về 1 user_id cụ thể) | `repositories/notifications.py:23-47, 56-73` |
| `retrieval_log` | Cả hai, không ràng buộc XOR ở DB | `repositories/connections.py:222-231`, migration `0002:37-44` không có CHECK |
| `orgs` | N/A (bảng tenant-nội-bộ = "team") | `repositories/orgs.py` |
| `org_members` | khóa bởi `(org_id, uid)` | `repositories/orgs.py:47-54` |
| `users` (= `auth.users`) | Không scope — bảng toàn cục, 1 dòng/người dùng Firebase | `repositories/users.py` |

### ❓/❌ Quy tắc XOR của `key_pool` có được enforce bằng code không?

✅ **Enforce bằng DB CHECK constraint** `key_pool_scope_xor` (`0002:88-91`). 
❌ **Không có code ứng dụng nào từng ghi `org_id` vào `key_pool`.** Đã grep toàn bộ `backend/` (ngoại trừ `tests/`): không có lệnh `INSERT INTO key_pool` hoặc `UPDATE key_pool` nào set `org_id`; `repositories/keys.py` (toàn bộ các hàm ghi: `get_or_create_key:12-29`, `set_key_vec:83-87`, `merge_key_into:110-125`) chỉ dùng `user_id`. → **Kết luận: cột `key_pool.org_id` và index `key_pool_org_id_key_text_key` (`0002:94-95`) là schema "chết" — tồn tại nhưng không được ghi/đọc bởi bất kỳ code nào.** Việc dedup key (ngưỡng cosine ≥ 0.9) trong team-share chỉ so khớp theo **`key_text`** giữa các `user_id` khác nhau (`repositories/connections.py:68-73`), không qua bảng `key_pool` scoped-by-org.

---

## 3. Ngữ nghĩa Share — COPY hay REFERENCE? (câu trả lời dứt khoát)

✅ **REFERENCE — không bao giờ copy `node_pool`**, cho cả hai kiểu share.

Bằng chứng trực tiếp từ comment migration (`0002:46-47`): *"share_node: extend the grant TARGET to allow an org, not just a person. This is the corrected design — no node_pool copy is created on share."* Và `node_pool` **không có cột** `origin_node_id`/`origin_user_id` (xác nhận ở mục 1.2) — về mặt schema không thể copy kiểu "node mới kèm origin" được.

**Share cho một người (person share)** — `repositories/shares.py:66-74`:
```sql
INSERT INTO share_node (owner_user_id, shared_user_id, node_id, message, permission, status, is_active)
VALUES (:owner, :recipient, :node_id, :message, 'view', 'pending', false) RETURNING id
```
Gọi từ `services/slack/team_handlers.py:262-270` (`submit_share` → hàm `create` nội bộ). **Không có `INSERT INTO node_pool`** ở bước này hay ở bước Accept (`repositories/shares.py:77-85`, chỉ `UPDATE share_node SET status='active', is_active=true`).

**Share cho team (org)** — `repositories/shares.py:132-140`:
```sql
INSERT INTO share_node (owner_user_id, org_id, node_id, permission, status, is_active, metadata)
VALUES (:owner, :org_id, :node_id, 'view', 'active', true, CAST(:metadata AS jsonb)) RETURNING id
```
Gọi từ `services/slack/team_handlers.py:298-331` (`submit_share_team`). Cũng **không** `INSERT INTO node_pool`.

**Đọc dữ liệu team-space** (`repositories/connections.py:190-219`, `get_team_graph`) xác nhận lại bằng docstring chính nó: *"every node actively shared to the org (**a reference to the original, never a copy**), tagged with its original owner"* (`connections.py:191-193`).

✅ **Code và docs/migration đồng thuận** — không phát hiện mâu thuẫn giữa code thực thi và comment/README trong repo này.

---

## 4. Giả định single-org / single-tenant

### 4.1 "Một người thuộc tối đa một team" — chủ đích, enforce bằng code, không bằng DB
`repositories/orgs.py:1-3` (docstring): *"Minimal on purpose: a user belongs to at most one org (**enforced here, not in the schema**)"*. Bằng chứng:
- `get_membership` dùng `ORDER BY m.joined_at LIMIT 1` — `repositories/orgs.py:11-22` → nếu DB có nhiều dòng `org_members` cho cùng `uid` (không bị DB chặn vì PK là `(org_id, uid)` không phải `(uid)`), code **chỉ lấy 1 theo thời gian join sớm nhất**, các org khác bị ẩn hoàn toàn.
- `services/team.py:35-42` (`create_team`) và `routers/team.py:80-89`: chặn tạo team thứ 2 bằng cách kiểm tra `get_membership` trước, nhưng đây là kiểm tra ở tầng ứng dụng (race condition giữa hai request đồng thời không bị chặn bởi DB — không có UNIQUE trên `org_members.uid`).
→ **Đây chính là giả định "1 user – 1 org" mà thiết kế mục tiêu (1 người có thể ở nhiều workspace, nhiều team) sẽ phải phá vỡ.**

### 4.2 Slack bot token/signing secret — biến môi trường đơn (singleton), không theo tenant trong *code*
- `core/config.py:67-68`: `SLACK_BOT_TOKEN: str | None`, `SLACK_SIGNING_SECRET: str | None` — biến môi trường đơn, không có bảng `orgs.slack_bot_token` hay tương tự.
- `services/slack/api.py:36-37`: `SlackClient.__init__` mặc định `self.token = token or settings.SLACK_BOT_TOKEN` — tất cả lệnh gọi Slack API (trừ `oauth_access`, dùng client_id/secret) dùng **cùng một token toàn cục** của process.
- Chính code tự ghi nhận giả định này: `routers/slack.py:150-151` (comment trong `oauth_callback`): *"The bot token in the response is not stored: the backend **still runs on the one SLACK_BOT_TOKEN**."*

**Tuy nhiên** (xem mục 9) — ở tầng hạ tầng, mỗi *tenant deployment* có secret riêng trong Secret Manager, nên "một token toàn cục" chỉ đúng *trong phạm vi một tenant/process* — vẫn là single-workspace-per-process, nhưng không phải single-workspace-cho-toàn-hệ-thống.

### 4.3 Cache cấp module, không khóa theo tenant
- `services/slack/handlers.py:46-48`: `_user_cache: dict[str, str] = {}` — Slack user id → Postgres user_id, cache **toàn process**, không có khóa theo workspace/tenant.
- `services/slack/team_handlers.py:35-36`: `_slack_id_cache: dict[str, str] = {}` — Postgres user_id → Slack user id, cùng kiểu cache toàn process.
→ Vì kiến trúc hiện tại là *một process backend/một workspace Slack* (silo theo tenant ở hạ tầng), rủi ro đụng độ dữ liệu giữa hai workspace trong cùng cache **không xảy ra trong kiến trúc hiện tại**, nhưng **sẽ là lỗ hổng trực tiếp nếu chuyển sang một backend process phục vụ nhiều workspace** (đúng như hướng multi-tenant-chung-DB mà yêu cầu audit nhắm tới) — Slack user ID không duy nhất toàn cục giữa các workspace khác nhau, nên cache này có thể trả nhầm user giữa hai tenant.

### 4.4 Không tìm thấy hardcode ID workspace/org
❌ Grep toàn bộ `backend/` cho ID dạng chuỗi cứng (team id, org id dạng UUID literal) trong code — không thấy hardcode nào ngoài giá trị cố định có chủ đích là namespace UUID để tạo `user_id` xem mục 5.

### 4.5 `TENANT_ID` đã tồn tại nhưng chỉ dùng cho tiền tố đường dẫn storage, không liên quan DB
`core/config.py:57-63`: biến `TENANT_ID` — comment: *"Identifies this deployment's tenant... Required in production"*. Dùng duy nhất ở `services/raw_storage.py:38` (`object_path`) để tạo tiền tố path file trên Cloud Storage. **Không liên quan gì đến lọc dữ liệu trong Postgres** — đây là tenant ID của *hạ tầng silo*, khác hoàn toàn khái niệm `tenant_id` cột DB mà thiết kế mục tiêu yêu cầu.

---

## 5. Identity mapping (Slack ↔ Firebase ↔ Postgres)

✅ **Không dùng email để map Slack ↔ web trực tiếp trong một bước** — mà qua 2 lớp:

1. **Firebase uid → Postgres `user_id`** (web app, không qua Slack): `core/auth.py:51-53`:
```python
def firebase_uid_to_user_id(firebase_uid: str) -> uuid.UUID:
    return uuid.uuid5(_USER_ID_NAMESPACE, firebase_uid)
```
`_USER_ID_NAMESPACE` là **UUID cố định hardcode** `6f0a2c9e-3b7a-4c8b-9c2b-2a6a8f2b7a10` (`core/auth.py:35`), comment ghi rõ: *"Do not change this value once real users exist."* Đây là map 1-1 tất định (`uuid5`) — không cần bảng lookup riêng. Dòng `auth.users` được upsert mỗi lần xác thực thành công: `core/auth.py:75-87` (`upsert_auth_user`), gọi từ dependency FastAPI `get_current_uid:90-125`.

2. **Slack user → Postgres `user_id`**: **qua email**, không qua bảng mapping riêng (không có cột `slack_user_id` nào trong `auth.users` hoặc bảng khác). `services/slack/handlers.py:55-69` (`resolve_user_id`):
```python
email = await client.user_email(slack_user_id)   # gọi Slack API users.info
user_id = users_repo.find_user_id_by_email(conn, email)   # SELECT ... WHERE lower(email)=lower(:email)
```
`repositories/users.py:5-15` — truy vấn `SELECT id FROM auth.users WHERE lower(email) = lower(:email) ORDER BY created_at DESC LIMIT 1`.

### ❓/⚠️ Điều gì xảy ra nếu email Slack ≠ email web, hoặc user Slack chưa từng đăng nhập web?

✅ **Trả lời rõ, có code dẫn chứng:** nếu không tìm thấy `user_id` tương ứng (email Slack không khớp bất kỳ `auth.users.email` nào), `resolve_user_id` trả `None`. Mọi luồng Slack sau đó hiển thị thông báo cố định `NOT_LINKED` — `services/slack/handlers.py:41-44`:
> *"I couldn't match your Slack account to a Neurix account. Sign in to the Neurix web app once with the same email as your Slack profile, then try again."*

và dừng xử lý (ví dụ `_linked_user` trong `team_handlers.py:83-91`). Trường hợp email Slack khác email web được xác nhận bằng test `test_team_share.py:166-167` ("C has never signed in... the inviter is told exactly that").

❓ **Chưa xác nhận được bằng code:** trường hợp một người có **hai email khác nhau trên hai workspace Slack khác nhau** nhưng cùng một email web app — vì không có bảng lưu "Slack user id ↔ Postgres user_id" cố định, mỗi lần gọi `resolve_user_id` phải gọi lại Slack API `users.info` để lấy email hiện tại rồi tra `auth.users`. Nếu người dùng đổi email Slack, lần tra cứu tiếp theo tự động theo email mới (không có state cũ cần dọn) — nhưng **chưa có test nào xác nhận hành vi này**, nên đánh ❓.

---

## 6. Truy vấn cần filter theo `tenant_id`, nhóm theo entry point

*(Lưu ý thuật ngữ: trong kiến trúc hiện tại, "tenant" = cả deployment/DB riêng theo workspace Slack, nên không có khái niệm lọc `tenant_id` trong SQL hiện tại. Bảng dưới liệt kê theo yêu cầu của thiết kế mục tiêu — những điểm SẼ cần thêm filter `tenant_id` nếu chuyển sang DB chung.)*

### 6.1 Slack slash commands / interactivity (`routers/slack.py`, `services/slack/handlers.py`, `services/slack/team_handlers.py`)
| Hàm | File:line | Lọc hiện tại |
|---|---|---|
| `resolve_user_id` | `handlers.py:55-69` | theo `email` (global, không theo workspace) |
| `_post_preview`/`confirm_draft`/`process_node` (ingest) | `ingest.py:86-196` | `user_id` |
| `open_share`/`submit_share`/`submit_share_team` | `team_handlers.py:222-362` | `user_id` + (nếu team) `org_id` qua `get_membership` |
| `handle_share_action` (Accept/Decline/Cancel) | `team_handlers.py:396-499` | `share_id` + so khớp `shared_user_id`/`owner_user_id` |
| `handle_stats` → `insights.collect` | `insights.py:13-23` | `user_id`, `org_id` (nếu có membership) |
| `open_raw_access`/`submit_raw_access`/`open_file`/`submit_file` | `team_handlers.py:551-676` | `user_id`, `org_id` (nếu team target) |
| `handle_ask` | `handlers.py` (gọi `ask_service.answer_question`) | `user_id` + `org_id` |

### 6.2 Web API (`routers/nodes.py`, `routers/team.py`)
| Endpoint | File:line | Lọc hiện tại |
|---|---|---|
| `GET /nodes` | `routers/nodes.py:52-54,19-39` | `user_id` (own + shared qua `shares_repo.visible_nodes`) |
| `GET /nodes/{id}/file` | `routers/nodes.py:57-65` | `user_id` qua `raw_files.find_fileable` |
| `GET /graph` | `routers/nodes.py:68-70` | `user_id` (`connections_repo.get_graph`) |
| `GET/POST /team`, `POST /team/members`, `DELETE /team/members/{id}` | `routers/team.py:68-121` | `user_id` → `org_id` qua membership |
| `GET /team/graph` | `routers/team.py:124-147` | `org_id` |

### 6.3 Background job / script
| Hàm | File:line | Lọc hiện tại |
|---|---|---|
| `scripts/run_migrations.py` | toàn file | không áp dụng (DDL, không phải truy vấn dữ liệu người dùng) |
| `services/ingest.process_node` (job xử lý sau Confirm — chạy trong background task FastAPI, không phải cron job riêng) | `ingest.py:132-196` | `user_id` |
| `services/share.persist_share_connections` | `share.py:48-85` | `share_id` → suy ra `user_id` hoặc `org_id` |

❓ **Không tìm thấy cron job / scheduled job độc lập nào** ngoài FastAPI `BackgroundTasks` (chạy trong cùng process ngay sau request) — đã kiểm tra `backend/scripts/` chỉ có `run_migrations.py`.

❌ **Không tìm thấy MCP server/tool nào trong repo `neurix_enterprise`** — không có thư mục `mcp_server` hoặc tương tự trong `backend/` (khác với repo `neurix` gốc, nơi có `mcp_server/`). Mọi quyền tool MCP liên quan Neurix trong session hiện tại (`Neurix_MCP`) không thuộc repo đang audit.

### 6.4 Raw SQL không lọc theo `user_id` hay `org_id` nào — rủi ro rò rỉ khi chuyển sang DB chung
Đã rà toàn bộ `repositories/*.py`. Các câu không filter theo user/org vì **chủ đích nhận ID cụ thể làm khóa chính** (không phải rò rỉ, vì truy vấn theo `id` đã được xác thực quyền ở tầng gọi phía trên):
- `repositories/nodes.py:41-52` (`get_node`, theo `node_id`) — **không tự kiểm tra quyền sở hữu trong hàm này**; việc kiểm tra `user_id` khớp được đẩy lên tầng gọi (ví dụ `services/share.py:88-97` `check_shareable_node` có so `row.user_id != user_id`; nhưng `team_handlers.py:591` `submit_raw_access` cũng tự so `node["user_id"] != user_id`). ⚠️ **Đây là điểm cần chú ý khi thêm `tenant_id`**: `get_node` phải được gọi kèm kiểm tra quyền ở MỌI nơi dùng nó — nếu một nơi gọi mới quên kiểm tra, sẽ rò rỉ node của người khác (hiện tại chỉ rò rỉ giữa user trong cùng 1 DB tenant; nếu DB chung nhiều tenant, sẽ rò rỉ xuyên tenant).
- `repositories/shares.py:42-43` (`get_share`, theo `share_id`) — tương tự, không tự kiểm tra quyền; các hàm gọi nó (`handle_share_action`, `_cancel`) tự so khớp `owner_user_id`/`shared_user_id`.
- `repositories/drafts.py:50-54` (`get_draft`), `:75-82` (`claim_draft`) — theo `draft_id`; quyền được kiểm tra ở `services/slack/handlers.py:231` (so `slack` field trong `preview`).

**Kết luận mục này:** không có truy vấn nào "quên" filter hoàn toàn (không ai SELECT toàn bảng không điều kiện), nhưng mô hình kiểm tra quyền hiện tại là **"theo ID + so sánh cờ sở hữu ở code gọi"**, chứ không có một lớp filter tập trung theo tenant/user ở tầng repository. Đây chính là loại rủi ro mà RLS (hàng rào thứ 2) được thiết kế mục tiêu đề xuất nhằm khắc phục.

---

## 7. Phạm vi dedup key và sinh connection

### 7.1 Dedup key theo embedding similarity ≥ `KEYS_SIMILARITY_SCORE` (0.9, `core/config.py:102`)
✅ **Chỉ scoped theo `user_id` của người thực hiện ingest, KHÔNG BAO GIỜ theo `org_id`** — dù cột `key_pool.org_id` tồn tại (xem mục 2).
Bằng chứng: `repositories/keys.py:90-107` (`search_similar_key`):
```sql
SELECT id, key_text, 1 - (key_vec <=> :vec) AS similarity FROM key_pool
WHERE user_id = :user_id AND key_vec IS NOT NULL AND id <> :exclude
  AND 1 - (key_vec <=> :vec) >= :threshold ORDER BY ... LIMIT 1
```
Gọi từ `services/ingest.py:158-164` (`process_node`), luôn truyền `user_id=user_id` (người sở hữu node đang ingest). → **Dedup luôn trong phạm vi 1 user, không bao giờ cross-user, không bao giờ cross-org** ở bước ingest cá nhân.

### 7.2 Dedup key khi tạo connection cho share (team hoặc person)
Khác với 7.1 — ở bước **sau khi share** (`share_connection_candidates`, `repositories/connections.py:33-83`), so khớp "key giống nhau" **dựa trên `key_text` giữa các `user_id` khác nhau**, vì mỗi người có `key_pool` riêng (comment dòng 49-51: *"key_pool is per user, so across owners 'the same key' means the same key_text rather than the same key_id"*). Đây là **so khớp có chủ đích cross-user**, nhưng **luôn giới hạn trong tập "targets" đã được xác định trước theo `recipient_id` (share cá nhân) hoặc `org_id` (share team)** — dòng `connections.py:58-63`:
```sql
targets AS (SELECT np.id FROM node_pool np WHERE np.id <> :node_id AND np.status <> 'FAILED' AND (
    (:recipient IS NOT NULL AND np.user_id = :recipient)
 OR (:org_id IS NOT NULL AND np.id IN (SELECT node_id FROM share_node WHERE org_id = :org_id AND status='active' AND is_active=true))
))
```
→ **Không có đường nào để so khớp key/connection vượt ra ngoài phạm vi "người nhận share" hoặc "org của share"** — đã kiểm tra, không có tham số nào khác được truyền vào ngoài `recipient_id`/`org_id` xác định rõ từ `share["shared_user_id"]`/`share["org_id"]` (`services/share.py:56-68`).

### 7.3 Sinh connection cá nhân (không qua share) — `services/ingest.py:170-184`
Dùng `keys_repo.nodes_sharing_key(conn, user_id=user_id, key_id=..., exclude_node_id=node_id)` — `repositories/keys.py:128-139`:
```sql
SELECT DISTINCT nk.node_id FROM node_keys nk JOIN node_pool np ON np.id = nk.node_id
WHERE nk.key_id = :key_id AND np.user_id = :user_id AND np.id <> :exclude AND np.status <> 'FAILED'
```
✅ Luôn lọc `np.user_id = :user_id` — **không thể cross-user**.

**Kết luận mục 7:** mọi truy vấn dedup/connection hiện tại đều scoped đúng theo chủ đích thiết kế (cá nhân → chỉ user đó; share → chỉ người nhận hoặc org đích xác định). Không tìm thấy truy vấn nào có thể vô tình so khớp xuyên user/org ngoài phạm vi dự kiến.

---

## 8. Thiết lập truy cập DB

### 8.1 Role Postgres mà backend dùng
❓ **Không xác định được tên role cụ thể từ code backend** — `db/session.py:25-32` chỉ đọc `settings.DB_USER` (biến môi trường, giá trị thực tế nằm trong Terraform/Secret Manager, ngoài phạm vi code backend). 
✅ Tuy nhiên xác nhận được: role app **không phải owner của schema** — `deploy/scripts/sql/grant_app_user.sql.tmpl:5-8` mô tả rõ hai role khác nhau:
- `__MIGRATION_ROLE__` = **`postgres`** (chạy migration, tạo bảng) — comment dòng 7-8: *"the role migrations run as (postgres — the same value passed as DB_USER to scripts.run_migrations)"*.
- `__DB_APP_USER__` = role riêng của tenant (`app_<tenant>`, comment dòng 5-6, tạo bởi `terraform/tenant/main.tf` qua `google_sql_user`) — chỉ được `GRANT SELECT, INSERT, UPDATE, DELETE` (không có DDL, không có quyền tạo bảng) — `grant_app_user.sql.tmpl:29-30`, cộng `ALTER DEFAULT PRIVILEGES` để tự động cấp quyền cho bảng tương lai (dòng 35-43).

→ ✅ **Backend app dùng role có quyền hạn chế (DML only), không phải owner/superuser.** Migration chạy bằng role khác (`postgres`), tách biệt khỏi app runtime.

### 8.2 Cách mở/pool connection
✅ `db/session.py:18-40`: dùng **Cloud SQL Python Connector** (`google.cloud.sql.connector.Connector`), driver `pg8000`, qua `creator=` callback — không hardcode IP/host TCP (comment dòng 3-7 xác nhận chủ đích thiết kế). `pool_size`/`max_overflow` lấy từ `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` (default 5/10). `@lru_cache(maxsize=1)` trên `get_engine()` → **1 engine/pool duy nhất cho toàn process** (phù hợp với silo 1-tenant-1-process hiện tại; sẽ là điểm phải sửa nếu 1 process cần nhiều pool cho nhiều tenant DB khác nhau).
Fallback dev: `LOCAL_DATABASE_URL` (connection string thường, không qua connector) — `session.py:42-48`.

### 8.3 Row-Level Security (RLS)
❌ **KHÔNG tìm thấy** bất kỳ `ENABLE ROW LEVEL SECURITY`, `CREATE POLICY`, hay `ROW LEVEL SECURITY` nào trong toàn bộ repo — đã grep case-insensitive trên tất cả file `.sql`, `.tf`, `.py` trong `neurix_enterprise`. **RLS hoàn toàn chưa được dùng ở bất kỳ đâu trong hệ thống hiện tại.** Toàn bộ kiểm soát quyền truy cập dữ liệu hiện nay nằm ở tầng ứng dụng (Python), không có hàng rào thứ 2 ở tầng DB như thiết kế mục tiêu yêu cầu.

---

## 9. Dữ liệu cài đặt Slack (bot token, signing secret)

✅ **Secret Manager, theo từng tenant deployment** — không phải 1 bộ cho toàn hệ thống, không lưu trong DB, không lưu trong code.

Bằng chứng:
- `core/config.py:65-68` comment: *"Slack App (Week 2). Both come from Secret Manager on Cloud Run (slack-bot-token / slack-signing-secret)."*
- `deploy/TENANT_RUNBOOK.md:116-117,139-160`: **Phase 3 ("slack-secrets") là bước thủ công riêng cho MỖI tenant** — tạo Slack App mới trong workspace của tenant đó (bước 1: *"api.slack.com/apps → Create New App → ... pick the tenant's workspace"*), lấy `Bot User OAuth Token` + `Signing Secret` rồi nhập trực tiếp vào Secret Manager qua `scripts/provision.sh slack-secrets ...` (input ẩn, không qua `.tfvars`/Terraform state — ghi rõ trong README dẫn chiếu tại dòng 159-160).
- ✅ Xác nhận lại qua `core/config.py:69-76`: `SLACK_CLIENT_ID`/`SLACK_CLIENT_SECRET` (cho OAuth "Add to Slack") cũng per-deployment, không bắt buộc (unset → endpoint OAuth trả 404, `routers/slack.py:152-153`).

→ **Số lượng Slack App/workspace mà code "giả định"**: **đúng 1 cho mỗi backend deployment** (= mỗi tenant hạ tầng). Code *bên trong* một deployment hoàn toàn không có khái niệm "nhiều workspace" — biến `SLACK_BOT_TOKEN` là global, như đã nêu ở mục 4.2. Việc liên kết `orgs.slack_team_id` (migration `0005`) chỉ ghi nhận **ID workspace nào đã cài app này** để hiển thị link "Open Neurix Workspace" trên web — **không dùng để chọn token nào sẽ gọi** (luôn dùng `SLACK_BOT_TOKEN` duy nhất).

⚠️ Rủi ro được chính README ghi nhận (`deploy/TENANT_RUNBOOK.md:278-285`, mục "Known unknowns"): **isolation của IAM condition trên bucket raw-file dùng chung (`dedicated_bucket = false`) CHƯA được kiểm chứng thực tế** — đánh dấu "UNVERIFIED" ngay trong tài liệu vận hành chính thức.

---

## 10. Test coverage cho isolation cross-user / cross-org

**Tổng số test hiện tại: 68 test function** (đếm bằng `grep -c "def test_"` trên toàn bộ `backend/tests/*.py`), **không phải 56 như giả định trong yêu cầu audit** — chênh lệch này cần làm rõ với chủ sở hữu repo (có thể do đếm ở version khác, hoặc gộp cả `neurix` gốc).

| File test | Số test |
|---|---|
| `test_auth_email_domain.py` | 5 |
| `test_config_validation.py` | 5 |
| `test_db_pool.py` | 2 |
| `test_llm_fallback.py` | 8 |
| `test_migrations.py` | 3 |
| `test_raw_storage_tenant_prefix.py` | 3 |
| `test_slack_edit_logging.py` | 2 |
| `test_slack_flow.py` | 12 |
| `test_slack_unit.py` | 7 |
| `test_team_share.py` | 10 |
| `test_web_api.py` | 1 |
| `test_web_polish.py` | 7 |
| `test_web_team.py` | 3 |
| **Tổng** | **68** |

### Các test liên quan trực tiếp đến isolation giữa user/org (trong CÙNG một DB tenant — không có test cross-*tenant* nào, vì tenant = DB riêng ở kiến trúc hiện tại):

✅ `test_team_share.py:186` `test_person_share_accept_end_to_end` — khẳng định rõ dòng 239: *"A's graph is unchanged: the edge lives in B's network"* (`assert graph(client, "/graph", people[A])["connections"] == []`).

✅ `test_team_share.py:307` `test_team_share_cross_member_dm_notify_scope_and_org_ask` — dòng 354: *"The team edge is org-scoped: it is not in either personal graph"* (`assert graph(client, "/graph", people[B])["connections"] == []`); dòng 364-366: thành viên ngoài team (`C`) bị chặn dùng team space, và `graph(client, "/team/graph", <uuid ngẫu nhiên>)` trả về rỗng cho user không thuộc org nào.

✅ `test_team_share.py:369` `test_shared_node_connections_appear_once_in_graph` — test hồi quy riêng cho rủi ro đếm trùng/leak connection khi chạy lại job nhiều lần.

✅ `test_team_share.py:291` `test_share_to_self_unlinked_or_foreign_node_is_refused` — khẳng định không thể share node của người khác (dòng 300-301: *"only share your own nodes"*).

✅ `test_web_team.py:81` `test_web_file_links_follow_view_raw` — kiểm tra quyền xem file gốc không bị rò rỉ giữa owner và người được share khi chưa cấp `can_view_raw` (dòng 105: `b_view() == ({x: False}, {t: False})`).

✅ `test_team_share.py:510` `test_raw_access_and_file_use_note_pickers` — dòng 536-537: người được share nhưng share còn "pending" (chưa accept) không thấy được file (`"🔒 You no longer have access..."`).

❓ **Không có test nào mô phỏng 2 tenant hạ tầng khác nhau** (2 DB riêng) — điều này đúng với kiến trúc hiện tại (test chạy trong 1 DB test duy nhất, silo theo tenant xảy ra ở tầng hạ tầng ngoài phạm vi pytest). Nếu chuyển sang DB chung, **đây chính là loại test mới hoàn toàn cần viết** — test hiện tại chỉ chứng minh isolation giữa *user/org trong cùng 1 DB*, không chứng minh được gì về isolation giữa *tenant* theo nghĩa thiết kế mục tiêu.

---

## Rủi ro cho việc migrate sang multi-tenant (DB chung)

1. **Đổi kiến trúc gốc, không phải thêm cột.** Hiện tại tenant = 1 DB + 1 Slack App + 1 Cloud Run service riêng (silo hạ tầng). Chuyển sang "DB chung + cột `tenant_id`" là đổi kiến trúc nền tảng, không phải chỉ thêm `tenant_id` vào các bảng đã có — mọi quy trình vận hành ở `deploy/TENANT_RUNBOOK.md` (provision/destroy per-tenant) sẽ phải viết lại.
2. **`SLACK_BOT_TOKEN`/`SLACK_SIGNING_SECRET` là biến global trong process** (`core/config.py:67-68`, `services/slack/api.py:36-37`) — nếu 1 process backend phải phục vụ nhiều workspace Slack (nhiều `slack_team_id`) trong DB chung, phải đổi sang tra token theo `tenant_id`/`org_id` cho MỌI lệnh gọi Slack API, không chỉ ở OAuth callback.
3. **Cache cấp module không khóa theo tenant** (`_user_cache` ở `handlers.py:48`, `_slack_id_cache` ở `team_handlers.py:36`) — Slack user ID không duy nhất toàn cục giữa các workspace khác nhau; nếu dùng chung 1 process cho nhiều workspace, cache này có thể trả nhầm `user_id` giữa hai tenant khác nhau.
4. **`get_membership`/`create_team` giả định "1 user – 1 org" bằng `LIMIT 1` và kiểm tra tầng ứng dụng** (`repositories/orgs.py:16`, `services/team.py:35-42`) — thiết kế mục tiêu cho phép 1 người ở nhiều team/nhiều workspace sẽ phải viết lại hoàn toàn logic này, không thể vá bằng cách thêm filter.
5. **`key_pool.org_id` là cột chết** (thêm ở `0002` nhưng không code nào ghi/đọc) — nếu giữ nguyên hướng "key theo org" cho thiết kế mới, phải implement lại từ đầu, không kế thừa được gì từ cột hiện có.
6. **Không có RLS ở bất kỳ đâu** — toàn bộ kiểm soát quyền nằm ở tầng Python, rải rác theo từng hàm gọi (`services/share.py:88-97`, `team_handlers.py:591-593`, v.v.) chứ không tập trung. Thiết kế mục tiêu yêu cầu RLS làm "hàng rào thứ 2" sẽ cần rà lại TỪNG truy vấn trong mục 6.4 — vì các hàm như `get_node`/`get_share`/`get_draft` hiện **không tự giới hạn theo quyền sở hữu**, chỉ truy vấn theo ID và để tầng gọi tự kiểm tra.
7. **`retrieval_log` không có CHECK XOR** như các bảng share/connection khác — nếu thêm `tenant_id`, cần quyết định ràng buộc ở đây có nên giống các bảng khác hay không (hiện bị bỏ sót, có thể là chủ đích hoặc thiếu sót).
8. **Isolation IAM condition trên bucket Cloud Storage dùng chung được chính team ghi "UNVERIFIED"** (`deploy/TENANT_RUNBOOK.md:278-285`) — nếu nhiều tenant dùng chung 1 bucket trong kiến trúc mới, đây là lỗ hổng tiềm ẩn đã biết nhưng chưa kiểm chứng.
9. **Chênh lệch số lượng test (68 thực tế vs. 56 giả định trong yêu cầu)** — chưa rõ nguyên nhân, cần xác minh với chủ sở hữu trước khi dùng số liệu test coverage làm căn cứ.

## Câu hỏi mở cho chủ sở hữu (owner)

1. Thiết kế mục tiêu ("1 Slack workspace = 1 tenant, DB chung") có ý định **giữ nguyên silo hạ tầng hiện tại** (mỗi tenant vẫn 1 Cloud SQL instance riêng) và chỉ thêm `tenant_id` để tách nhiều "sub-tenant" trong CÙNG 1 workspace không? Hay có ý định **gộp nhiều Slack workspace vào 1 DB vật lý duy nhất**? Hai hướng này kéo theo mức độ thay đổi code rất khác nhau (mục rủi ro #2, #3 chỉ phát sinh ở hướng thứ hai).
2. `key_pool.org_id` (cột chết) — có nên xóa trong migration tiếp theo, hay giữ lại để tái sử dụng cho thiết kế team-key mới?
3. Vì sao `retrieval_log` không có CHECK XOR `user_id`/`org_id` như các bảng share/connection khác — chủ đích hay thiếu sót cần vá trước khi migrate?
4. 68 test thực tế vs. 56 test giả định trong yêu cầu — cần đối soát lại nguồn số liệu 56 từ đâu.
5. Có kế hoạch xử lý trường hợp **1 Firebase uid nhưng đổi email Slack giữa các lần dùng** (mục 5, phần ❓) trước khi chuyển sang mô hình "1 người – nhiều workspace" không — hiện tại hoàn toàn dựa vào tra email mỗi lần, không có bảng lưu quan hệ Slack-user-id ↔ Postgres-user-id cố định.
6. Rủi ro "UNVERIFIED" về IAM condition trên bucket chia sẻ (mục 9) có cần xác minh trước khi thiết kế lại raw-storage cho multi-tenant, hay sẽ đổi hẳn sang bucket-riêng-mỗi-tenant trong thiết kế mới?
