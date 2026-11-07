# ⚽ DraftFC — Plataforma de Fantasy Draft Multiplayer (EA Sports FC 26 / 27)

O **DraftFC** é uma plataforma completa de *Fantasy Draft* em formato **Snake Draft** (inspirada no *Sleeper*) desenvolvida para campeonatos presenciais e online de **EA Sports FC 26 e EA Sports FC 27** no PC.

O sistema integra extração de dados atualizados do SoFIFA, validação de regras de elenco, sala de draft multiplayer em tempo real via WebSockets com **Draft Board colorido por setor** e um motor de exportação binária/RDBM que transfere automaticamente os elencos escolhidos para dentro do arquivo `Squads` do jogo.

---

## ✨ Principais Funcionalidades

* **🕷️ Scraper SoFIFA Resiliente (Fase 1):** Extração automatizada de clubes, ligas e atletas com suporte a paginação, normalização monetária/posicional (PT-BR e EN), armazenamento em SQLite (`data/draftfc_local.db`) e contorno de bloqueios Cloudflare (`curl_cffi` + fallback via navegador nativo).
* **⚖️ Motor de Regras & Snake Draft (Fases 2 e 3):** Geração da ordem *Snake* (`1 → N` nas rodadas ímpares, `N → 1` nas rodadas pares), travas configuráveis (limite de jogadores por clube, liga ou nacionalidade) e fila de prioridades (*Queue*) por participante.
* **🌐 Arena Multiplayer em Tempo Real (Fases 4 e 6):**
  * Criação e listagem de salas ativas com proteção por senha.
  * Sincronização instantânea via **WebSockets nativos do FastAPI**.
  * Reconexão inteligente: se um participante fechar o navegador ou cair da rede, basta entrar novamente com o **mesmo nome de Time/Manager** para recuperar o assento e o elenco.
  * **Draft Board Estilo Sleeper:** Matriz 2D interativa colorida por posição (`GK` Goleiro, `DEF` Defesa, `MID` Meio-Campo e `ATT` Ataque).
  * **Poderes de Comissário (`👑`):** Sorteio aleatório da ordem do draft (`🎲`), desfazer última escolha (`↩`), escolher atletas em nome de outros managers e exportação imediata do histórico em `.csv`.
* **🎮 Exportador Direto para o EA FC no PC (Fase 5):** Patcher automatizado para tabelas do **RDBM** (`teamplayerlinks.txt`, `default_teamsheets.txt`, `leagueteamlinks.txt` e `playerloans.txt`) com *backfill* automático nos clubes de origem, preservação de seleções nacionais e remoção de travas de empréstimo.

---

## 🛠️ Pré-requisitos

* **Python 3.11+**
* **Node.js 18+ (LTS)** e `npm`
* **RDBM (Revolution DB Master)** — necessário apenas na máquina que aplicará o arquivo `Squads` no EA Sports FC 26/27 de PC.

---

## 🚀 Instalação e Configuração Rápida

### 1. Clonar o repositório e preparar o ambiente Python (Backend)

No **Windows PowerShell**, na raiz do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 2. Instalar as dependências do Frontend (React + Vite)

Em outro terminal PowerShell:

```powershell
cd frontend
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
npm install
```

---

## 🏆 Como Rodar no Dia do Campeonato

Para permitir que todos os participantes entrem na sala pelo próprio celular ou notebook conectados na mesma rede Wi-Fi (ou via `localhost`), mantenha **dois terminais abertos**:

### Terminal 1 — Servidor Backend (FastAPI + WebSockets)
Na raiz do projeto (com o `.venv` ativo):

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```
* Documentação interativa da API: `http://127.0.0.1:8000/docs`

### Terminal 2 — Interface Visual (Vite + React)
Dentro da pasta `frontend`:

```powershell
cd frontend
npm run dev -- --host
```
* Acesso local no PC do Host: `http://localhost:5173`
* Acesso para amigos no mesmo Wi-Fi: use o endereço `Network: http://192.168.X.X:5173` exibido no terminal do Vite.

---

## 👑 Guia de Operação do Comissário durante o Draft

1. **Criar a Sala:** Na tela inicial (`http://localhost:5173`), preencha o nome da liga, a quantidade de participantes, o número de rodadas e o limite de jogadores por clube, e clique em **Criar Sala Multiplayer & Abrir Lobby**.
2. **Entrada dos Managers:** Compartilhe o **Código da Sala** (6 caracteres no topo da tela). Cada participante entra informando seu nome.
3. **Sortear a Ordem do Draft:** Quando todos estiverem na sala (e antes da 1ª escolha), clique no botão roxo **🎲 Sortear Ordem do Draft** na barra do Comissário para embaralhar os assentos de `1` a `N`.
4. **Iniciar e Conduzir as Escolhas:**
   * Clique em **▶ Iniciar Draft Agora**.
   * Cada participante escolhe no seu próprio turno clicando em **DRAFTAR** (ou o próprio Comissário pode selecionar atletas para qualquer participante mantendo a caixa **Escolher p/ Outros Managers** marcada).
   * Caso alguém clique no jogador errado, basta clicar em **↩ Voltar Última Escolha** para devolver o atleta ao pool e retornar o turno imediatamente.
5. **Exportar Planilha do Draft:** A qualquer momento ou ao final da última rodada, clique em **📥 Exportar CSV** no topo da tela para salvar todas as escolhas (`overall_pick`, `round`, `manager`, `player_id`, `short_name`, `position`, `overall`, `club_name`).

---

## 📦 Banco de Dados de Jogadores (Scraper SoFIFA)

Caso precise atualizar ou expandir a base de jogadores em `data/draftfc_local.db`:

```powershell
# Teste rápido (2 páginas = 150 principais jogadores por Overall)
python -m scraper.cli --edition fc26 --gender male --max-pages 2

# Extração de mais páginas retomando de onde parou (ex: 10 páginas = 750 jogadores)
python -m scraper.cli --edition fc26 --gender male --max-pages 10 --resume
```

Se você já tiver os dados coletados na edição `fc27` e quiser espelhá-los instantaneamente para `fc26` no SQLite:

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/draftfc_local.db'); [c.execute(f'INSERT OR IGNORE INTO {t} SELECT \"fc26\", ' + ','.join([r[1] for r in c.execute(f'PRAGMA table_info({t})').fetchall() if r[1]!=\"edition\"]) + f' FROM {t} WHERE edition=\"fc27\"') for t in ('clubs','players')]; c.commit()"
```

---

## 🎮 Exportação e Injeção dos Elencos no EA Sports FC 26 (PC)

1. **Backup e Extração no RDBM:**
   * Copie seu arquivo `Squads...` original de `%LOCALAPPDATA%\EA SPORTS FC 26\settings` para `data/backup_squads`.
   * Abra o arquivo `Squads` no **RDBM** e exporte (`Export to TXT`) as 4 tabelas abaixo para a pasta `data/rdbm_clean/`:
     * `teamplayerlinks.txt`
     * `default_teamsheets.txt`
     * `leagueteamlinks.txt`
     * `playerloans.txt`
2. **Executar o Patcher do DraftFC:**
   ```powershell
   python -m backend.exporter.cli --demo-from-db --edition fc26 --host-teams 305,306 --rdbm-dir data/rdbm_clean --out-dir data/exports/rdbm_patched
   ```
3. **Importar de volta no RDBM e Jogar:**
   * No RDBM, use **Import from TXT** nas tabelas `teamplayerlinks`, `default_teamsheets` e `playerloans` apontando para os arquivos gerados em `data/exports/rdbm_patched/`.
   * Salve o arquivo `Squads` no RDBM e copie-o de volta para `%LOCALAPPDATA%\EA SPORTS FC 26\settings`.
   * No EA FC 26, vá em **Configurações → Perfil → Carregar Elencos**, abra o modo **Amistoso (Kick-Off)** com a opção **Forma ao Vivo (Live Form) DESATIVADA** e boa partida!

---

## 🧪 Suíte de Testes Automatizados

Para rodar todos os testes de unidade e integração (Scraper, Motor de Regras, Auto-Draft, API Local, Exportador RDBM e Concorrência/WebSockets):

```powershell
pytest -v
```