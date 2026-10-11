-- =====================================================================
-- FOTOS DA MALETA (conferência de saída)
-- Cria a tabela que registra cada foto e o bucket do Supabase Storage onde
-- os arquivos ficam. Rode uma vez no SQL Editor; pode rodar de novo sem problema.
-- =====================================================================
begin;

-- 1) Registro de cada foto (o arquivo em si fica no Storage) -------------
create table if not exists public.parcerias_envio_arquivos (
    id               bigint generated always as identity primary key,
    envio_id         bigint not null references public.parcerias_envios(id) on delete cascade,
    etapa            text   not null default 'saida' check (etapa in ('saida', 'retorno')),
    caminho_storage  text   not null,      -- ex.: envio_12/saida/3f9a...c1.jpg
    nome_arquivo     text,                 -- nome original, só para exibir
    mime_type        text,
    tamanho_bytes    bigint,
    criado_em        timestamptz not null default now()
);

create index if not exists ix_parcerias_envio_arq_envio
    on public.parcerias_envio_arquivos (envio_id);

alter table public.parcerias_envio_arquivos disable row level security;

-- 2) Bucket 'fotos_maletas' (público, no mesmo modelo de 'fotos_produtos') ---
--    Limite de 10 MB por arquivo e só imagens. O app já reduz cada foto
--    (~300 KB) antes de enviar; o limite é só uma proteção.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'fotos_maletas', 'fotos_maletas', true, 10485760,
    array['image/jpeg', 'image/png', 'image/webp']
)
on conflict (id) do update
   set public = true,
       file_size_limit = excluded.file_size_limit,
       allowed_mime_types = excluded.allowed_mime_types;

-- 3) Permissões do app (chave anon) restritas a ESTE bucket -----------------
--    Se o app usa a chave service_role, estas políticas são desnecessárias,
--    mas não fazem mal.
drop policy if exists "fotos_maletas_enviar"  on storage.objects;
drop policy if exists "fotos_maletas_ler"     on storage.objects;
drop policy if exists "fotos_maletas_apagar"  on storage.objects;

create policy "fotos_maletas_enviar" on storage.objects
    for insert to public with check (bucket_id = 'fotos_maletas');
create policy "fotos_maletas_ler" on storage.objects
    for select to public using (bucket_id = 'fotos_maletas');
create policy "fotos_maletas_apagar" on storage.objects
    for delete to public using (bucket_id = 'fotos_maletas');

commit;

notify pgrst, 'reload schema';

-- 4) Conferência: deve listar o bucket e a tabela
select 'bucket' as item, id as nome, public::text as detalhe from storage.buckets where id = 'fotos_maletas'
union all
select 'tabela', table_name, 'ok' from information_schema.tables
 where table_schema = 'public' and table_name = 'parcerias_envio_arquivos';
