-- One named block per result; `setup` runs first over the runs in `sel_ids`.
-- Every result reads `sel` (the selected sessions' runs) and drops memray runs except R15.
-- page_text is never read here: no export carries course text.

-- name: setup
create or replace temp view sel as select r.* from run r join sel_ids using (id);
create or replace temp view power_x as
select run, sum((t_s - prev_t) * (combined_mw + prev_mw) / 2) / 1000 as energy_raw_j,
       avg(cpu_mw) as cpu_mw, avg(gpu_mw) as gpu_mw, avg(ane_mw) as ane_mw,
       avg(gpu_active) as gpu_active
from (select *, lag(t_s) over w as prev_t, lag(combined_mw) over w as prev_mw
      from sample_power window w as (partition by run order by t_s))
group by run;
create or replace temp view proc_x as
select run, max(rss_mb) as peak_rss_mb, max(footprint_mb) as peak_footprint_mb,
       max(gpu_mem_mb_system) as peak_gpu_mem_mb_system,
       (max(cpu_s) - min(cpu_s)) / nullif(max(t_s) - min(t_s), 0) * 100 as cpu_pct
from sample_proc group by run;
create or replace temp view run_res as
select s.id as run, s.bench, s.arm, s.subject, s.run_no, s.memray, s.returncode, s.wall_s,
       s.load_s, s.items, s.device, s.embed_s, s.rerank_s, s.pairs, p.peak_rss_mb,
       p.peak_footprint_mb, p.peak_gpu_mem_mb_system, p.cpu_pct, e.cpu_mw, e.gpu_mw, e.ane_mw,
       e.gpu_active, e.energy_raw_j - s.idle_combined_mw * s.wall_s / 1000 as energy_j
from sel s left join proc_x p on p.run = s.id left join power_x e on e.run = s.id;
create or replace temp view acc_pages as
select s.arm, p.* exclude (run) from page p join sel s on s.id = p.run
where s.bench = 'extract' and not s.memray and s.run_no = 1 and p.f1 is not null;

-- name: f1_lists
select arm, kind, lang, list(f1) from acc_pages group by all;

-- name: R1
select a.arm, a.kind || '/' || a.lang as cell, count(*) as pages, avg(a.f1) as f1,
       any_value(c.f1_lo) as f1_lo, any_value(c.f1_hi) as f1_hi,
       avg(a."precision") as word_precision, avg(a.recall) as word_recall,
       avg(a.bigram_f1) as bigram_f1, avg(a.diacritic_recall) as diacritic_recall
from acc_pages a left join f1_ci c using (arm, kind, lang)
group by all order by all;

-- name: R2
select arm, kind || '/' || lang as cell, sha256, page, f1 from acc_pages order by all;

-- name: R3
with acc as (select arm, avg(f1) as f1 from acc_pages group by arm),
cost as (select s.arm, sum(p.ms) / 1000 / count(*) as s_per_page
         from page p join sel s on s.id = p.run
         where s.bench = 'extract' and not s.memray and p.error is null group by s.arm),
energy as (select arm, median(energy_j / nullif(items, 0)) as j_per_page from run_res
           where bench = 'extract' and not memray group by arm)
select * from acc full join cost using (arm) full join energy using (arm) order by arm;

-- name: R4
select s.arm, s.run_no, p.ms, s.load_s from page p join sel s on s.id = p.run
where s.bench = 'extract' and not s.memray and p.error is null order by all;

-- name: R5
select r.bench, r.arm, r.run_no, x.t_s, x.rss_mb, x.footprint_mb, x.gpu_mem_mb_system
from sample_proc x join run_res r on r.run = x.run
where r.bench in ('extract', 'index') and not r.memray order by r.bench, r.arm, r.run_no, x.t_s;

-- name: R6
select bench, arm, subject, run_no, items, wall_s, cpu_pct, gpu_active, cpu_mw, gpu_mw, ane_mw,
       energy_j, energy_j / nullif(items, 0) as j_per_item
from run_res where bench in ('extract', 'index') and not memray order by all;

-- name: R7
select s.bench, s.arm, s.run_no, s.returncode,
       count(*) filter (where p.capped) as capped_pages,
       count(*) filter (where p.error is not null) as failed_items,
       0 as timed_out
from sel s left join page p on p.run = s.id
where s.bench = 'extract' and not s.memray group by all
union all
select s.bench, s.arm, s.run_no, s.returncode, 0,
       count(*) filter (where f.status <> 'ok'),
       count(*) filter (where f.error ilike '%timed out%')
from sel s left join file f on f.run = s.id
where s.bench = 'index' and not s.memray group by all
order by all;

-- name: R8
select s.subject, s.arm, s.run_no, s.wall_s, s.items as chunks, count(f.filename) as files,
       sum(f.extract_s) as extract_s, sum(f.embed_s) as embed_s,
       s.wall_s - coalesce(sum(f.extract_s), 0) - coalesce(sum(f.embed_s), 0) as other_s
from sel s left join file f on f.run = s.id
where s.bench = 'index' and not s.memray and s.returncode = 0
group by s.subject, s.arm, s.run_no, s.wall_s, s.items order by all;

-- name: R9
select s.subject, s.arm, s.run_no, avg(q.hit_within1_at8::int) as hit_within1_at8,
       count(*) as queries
from query q join sel s on s.id = q.run
where s.bench = 'retrieval' and not s.memray group by all order by all;

-- name: R10
select arm, device, run_no, load_s, items as chunks, embed_s, items / embed_s as chunks_per_s,
       pairs, rerank_s, pairs / rerank_s as pairs_per_s
from run_res where bench = 'embed' and not memray order by all;

-- name: R11
select s.arm, quantile_cont(q.latency_ms, 0.5) as p50_ms, quantile_cont(q.latency_ms, 0.95) as p95_ms,
       count(*) as queries
from query q join sel s on s.id = q.run
where s.bench = 'embed' and not s.memray group by all order by all;

-- name: R12
select arm, run_no, peak_rss_mb, peak_footprint_mb, peak_gpu_mem_mb_system, cpu_pct, gpu_active,
       energy_j, energy_j * 1000 / nullif(items, 0) as j_per_1000_chunks
from run_res where bench = 'embed' and not memray order by all;

-- name: R13
select ra.run_no, a.item_kind, a.item_id, a.dense_cos, a.sparse_top10_overlap,
       a.rerank_top5_overlap, a.max_score_diff
from agreement a join sel ra on ra.id = a.run_a order by all;

-- name: R14
select s.arm, s.subject, s.run_no, avg(q.hit_within1_at8::int) as hit_within1_at8
from query q join sel s on s.id = q.run
where s.bench = 'embed' and not s.memray group by all order by all;

-- name: R15
select s.bench, s.arm, s.run_no, a.process, a.kind, a.path, a.peak_heap_mb
from artifact a join sel s on s.id = a.run where a.kind like 'memray%' order by all;
