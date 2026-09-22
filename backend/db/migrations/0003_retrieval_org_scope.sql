-- =============================================================================
-- 0003_retrieval_org_scope.sql
-- Extends retrieve_candidate_nodes for org scope. Adds one optional trailing
-- parameter (org_id, default NULL) and widens the shared_nodes CTE only —
-- every other line is byte-for-byte identical to the 0001 version.
--
-- CREATE OR REPLACE FUNCTION with a different parameter list creates a
-- distinct overload in Postgres rather than replacing the original 6-arg
-- function — both coexist, which is fine since existing call sites using 6
-- positional args keep working unchanged.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.retrieve_candidate_nodes(
  user_id uuid, query_text text, query_embedding vector,
  scope text DEFAULT 'merged'::text, match_count integer DEFAULT 30,
  query_language text DEFAULT 'en'::text, org_id uuid DEFAULT NULL
)
RETURNS TABLE(id uuid, user_id uuid, title text, ai_title text, source text, size_tokens integer, summary text, summary_vec vector, metadata jsonb, created_at timestamp with time zone, updated_at timestamp with time zone, text_search_vec tsvector, source_scope text, candidate_type text, similarity double precision, lexical_rank double precision)
LANGUAGE sql
STABLE
AS $function$
with shared_nodes as (
    select sn.node_id
    from public.share_node sn
    where (
        sn.shared_user_id = retrieve_candidate_nodes.user_id
        or (retrieve_candidate_nodes.org_id is not null and sn.org_id = retrieve_candidate_nodes.org_id)
    )
      and sn.is_active = true
      and (sn.expires_at is null or sn.expires_at > now())
),
scoped_nodes as (
    select
        np.id,
        np.user_id,
        case
            when np.user_id = retrieve_candidate_nodes.user_id then 'owned'
            else 'shared'
        end as source_scope
    from public.node_pool np
    where (
        retrieve_candidate_nodes.scope = 'owned'
        and np.user_id = retrieve_candidate_nodes.user_id
    ) or (
        retrieve_candidate_nodes.scope = 'shared'
        and np.id in (select node_id from shared_nodes)
    ) or (
        retrieve_candidate_nodes.scope = 'merged'
        and (
            np.user_id = retrieve_candidate_nodes.user_id
            or np.id in (select node_id from shared_nodes)
        )
    )
),
semantic_candidates as (
    select
        np.*,
        sc.source_scope,
        'semantic_node'::text as candidate_type,
        (1 - (np.summary_vec <=> retrieve_candidate_nodes.query_embedding))::double precision as similarity,
        null::double precision as lexical_rank
    from public.node_pool np
    join scoped_nodes sc on sc.id = np.id
    where np.summary_vec is not null
    order by np.summary_vec <=> retrieve_candidate_nodes.query_embedding asc
    limit least(retrieve_candidate_nodes.match_count, 200)
),
lexical_candidates as (
    select
        np.*,
        sc.source_scope,
        'lexical_node'::text as candidate_type,
        null::double precision as similarity,
        case
            when retrieve_candidate_nodes.query_language = 'vi' then
                ts_rank_cd(
                    to_tsvector('simple', unaccent(coalesce(np.title, '') || ' ' || coalesce(np.summary, ''))),
                    websearch_to_tsquery('simple', unaccent(retrieve_candidate_nodes.query_text))
                )
            else
                ts_rank_cd(
                    np.text_search_vec,
                    websearch_to_tsquery('english', retrieve_candidate_nodes.query_text)
                )
        end::double precision as lexical_rank
    from public.node_pool np
    join scoped_nodes sc on sc.id = np.id
    where
        case
            when retrieve_candidate_nodes.query_language = 'vi' then
                to_tsvector('simple', unaccent(coalesce(np.title, '') || ' ' || coalesce(np.summary, '')))
                    @@ websearch_to_tsquery('simple', unaccent(retrieve_candidate_nodes.query_text))
            else
                np.text_search_vec @@ websearch_to_tsquery('english', retrieve_candidate_nodes.query_text)
        end
    order by lexical_rank desc
    limit least(retrieve_candidate_nodes.match_count, 200)
),
key_candidates as (
    select
        np.*,
        sc.source_scope,
        'key_node'::text as candidate_type,
        (1 - (kp.key_vec <=> retrieve_candidate_nodes.query_embedding))::double precision as similarity,
        null::double precision as lexical_rank
    from public.key_pool kp
    join public.node_keys nk on nk.key_id = kp.id
    join scoped_nodes sc on sc.id = nk.node_id
    join public.node_pool np on np.id = sc.id
    where kp.key_vec is not null
    order by kp.key_vec <=> retrieve_candidate_nodes.query_embedding asc
    limit least(retrieve_candidate_nodes.match_count, 200)
),
all_candidates as (
    select * from semantic_candidates
    union all
    select * from lexical_candidates
    union all
    select * from key_candidates
)
select distinct on (id)
    id, user_id, title, ai_title, source, size_tokens, summary, summary_vec,
    metadata, created_at, updated_at, text_search_vec, source_scope,
    candidate_type, similarity, lexical_rank
from all_candidates
order by id, similarity desc nulls last, lexical_rank desc nulls last
limit least(retrieve_candidate_nodes.match_count, 200);
$function$;
