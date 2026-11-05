-- SW_team10 Supabase 스키마
-- Supabase 대시보드 > SQL Editor 에 붙여 넣고 한 번 실행한다. 여러 번 실행해도 안전하다.
--
-- 접근 원칙
-- - 모든 테이블에 RLS를 켜고 정책을 만들지 않는다 → 브라우저용 publishable/anon 키로는 읽기·쓰기 불가.
-- - 서버만 secret 키(RLS 우회)로 접근한다. (.env의 SUPABASE_SECRET_KEY)
--
-- 보관 정책 (SRS NFR-003, DATA-09 잠정안)
-- - 질의 문장과 처리 경로 중심으로 저장하고, 세션 ID와 좌표는 저장하지 않는다.
-- - 하루 1회 사전 후보를 집계한 뒤 질의 로그를 삭제해 1일을 넘겨 보관하지 않는다. (아래 4번)

-- ---------------------------------------------------------------------------
-- 1) 질의 로그: /api/v1/recommend 요청 한 건 = 한 줄
-- ---------------------------------------------------------------------------
create table if not exists public.query_logs (
    id             bigint generated always as identity primary key,
    created_at     timestamptz not null default now(),
    message        text not null,                -- 질의 문장
    route          text,                         -- 처리 경로: local / llm / llm_fallback / api
    mode           text,                         -- 화면에서 고른 방식: search / recommend / nearby / null
    intent         text,                         -- recommend_place, search_place, clarify ...
    unknown_terms  text[] not null default '{}', -- Local Router 사전에 없던 단어 (사전 보강 후보)
    result_count   integer not null default 0
);

create index if not exists query_logs_created_at_idx on public.query_logs (created_at desc);
create index if not exists query_logs_unknown_terms_idx on public.query_logs using gin (unknown_terms);
alter table public.query_logs enable row level security;

-- ---------------------------------------------------------------------------
-- 2) 사전 후보: 하루 1회 집계 결과를 쌓고, 사람이 승인한 단어만 사전에 반영한다
-- ---------------------------------------------------------------------------
create table if not exists public.term_candidates (
    term              text primary key,
    first_seen        timestamptz not null default now(),
    last_seen         timestamptz not null default now(),
    occurrences       integer not null default 0,
    proposed_type     text,      -- food / region / condition / noise
    proposed_node     text,      -- 예: chinese, 부산진구 부전동
    status            text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
    reviewed_at       timestamptz
);
alter table public.term_candidates enable row level security;

-- ---------------------------------------------------------------------------
-- 3) 사전에 없던 단어 집계 (일괄 작업이 읽는 뷰)
--    security_invoker: 뷰를 조회하는 사람의 권한으로 실행 → RLS가 그대로 적용된다
-- ---------------------------------------------------------------------------
create or replace view public.unknown_term_stats
with (security_invoker = true) as
select
    term,
    count(*)        as occurrences,
    min(created_at) as first_seen,
    max(created_at) as last_seen
from public.query_logs, unnest(unknown_terms) as term
group by term;

-- ---------------------------------------------------------------------------
-- 4) 하루 1회: 사전 후보 집계 후 집계한 질의 로그 삭제 (매일 04:00 KST = UTC 19:00)
--    집계한 줄은 바로 지우므로 다음 날 다시 세지 않고, 질의 로그는 최대 1일만 남는다.
--    Database > Extensions 에서 pg_cron을 켠 뒤 아래 주석을 풀고 실행한다.
-- ---------------------------------------------------------------------------
-- select cron.schedule(
--     'daily-term-candidates-and-purge', '0 19 * * *',
--     $$
--     insert into public.term_candidates (term, first_seen, last_seen, occurrences)
--     select term, first_seen, last_seen, occurrences from public.unknown_term_stats
--     on conflict (term) do update
--         set last_seen = excluded.last_seen,
--             occurrences = public.term_candidates.occurrences + excluded.occurrences;
--     delete from public.query_logs where created_at <= now();
--     $$
-- );
