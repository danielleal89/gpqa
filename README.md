# README

## DOCUMENTAÇÃO DA PIPELINE

<https://fontes.intranet.bb.com.br/aic/publico/atendimento/-/wikis/Pipelines/Python>

## Banco no Cloudflare (D1)

Este projeto usa SQLite por padrão, mas também pode consultar um banco Cloudflare D1 via API HTTP.

### Variáveis necessárias

Preencha um `.env` (pode partir de [.env.example](file:///Users/geinttestesdecanais/GitHub/plm-pixel-test/.env.example)):

- `DB_DRIVER=cloudflare_d1`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_D1_DATABASE_ID` (UUID do banco D1)
- `CLOUDFLARE_API_TOKEN` (token com permissão de D1)
- `CLOUDFLARE_API_BASE_URL` (default `https://api.cloudflare.com/client/v4`)
- `ENABLE_RUNTIME_CACHE` (default `1` quando usar D1)
- `RUNTIME_CACHE_TTL_SECONDS` (default `5`)
- `LOG_ROUTE_TIMINGS` (`1` para imprimir o tempo de cada rota no terminal)

### Criar as tabelas no D1

Depois de preencher o `.env`, rode:

```bash
python scripts/init_db.py
```

Isso cria as tabelas e tenta aplicar as colunas adicionais do projeto diretamente no D1.

### Criar o primeiro usuário administrador

Como a aplicação exige login para acessar o gerenciamento de usuários, em um banco vazio o primeiro acesso deve ser criado por script:

```bash
python scripts/create_first_user.py --name "Administrador" --key C1234567 --password "SuaSenhaAqui"
```

Observações:

- O login usa `key` + `password`.
- A `key` deve ter 8 caracteres.
- Esse script já cria o usuário como administrador.

### Exportar o SQLite local para SQL (para importar no D1)

Exemplo:

```bash
python scripts/export_sqlite_dump.py --db banco.db --out /tmp/banco_dump.sql
```

Depois, importe esse arquivo no D1 usando o Wrangler/console do Cloudflare.

### Ordem sugerida de migração

1. Preencher o `.env` com as credenciais do D1.
2. Rodar `python scripts/init_db.py`.
3. Exportar o banco local com `python scripts/export_sqlite_dump.py --db banco.db --out /tmp/banco_dump.sql`.
4. Importar o dump no D1.
5. Reiniciar a aplicação já apontando para o D1.

## Arquivos no Cloudflare (R2)

Os uploads podem ser salvos no Cloudflare R2. Se o R2 não estiver configurado, a aplicação continua usando `static/uploads/...` localmente.

### Variáveis necessárias

- `R2_BUCKET_NAME`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_ENDPOINT`
- `R2_PUBLIC_BASE_URL` (opcional, apenas se o bucket tiver URL pública)

### Como funciona

- Com `R2_*` preenchido, novos uploads de documentações, imagens de notas do Kanban e imagens dos detalhes de subtarefas passam a ser gravados no R2.
- Se `R2_PUBLIC_BASE_URL` não for informado, a aplicação lê os arquivos pelo endpoint interno `/storage/file`.
- Se `R2_PUBLIC_BASE_URL` estiver informado, links públicos podem ser gerados diretamente para os arquivos do bucket.
