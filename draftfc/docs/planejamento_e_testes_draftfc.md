# Planejamento e Estratégia de Testes - DraftFC

Este documento detalha o escopo, o planejamento de execução e os cenários de teste para o desenvolvimento da plataforma de draft para o EA FC (PC). O projeto está dividido em fases lógicas, culminando em um MVP local antes da implementação do modo multiplayer em tempo real.

## Fase 1: Mapeamento de Jogadores (Scraping SoFIFA)

**Objetivo:** Criar um banco de dados atualizado com todos os jogadores disponíveis no jogo usando o SoFIFA.

* **Planejamento:**

  * **Ferramentas:** Python com `BeautifulSoup` (para parseamento HTML) e `requests` ou `Playwright` (caso haja renderização dinâmica pesada).

  * **Dados Alvo:** ID do SoFIFA, Nome, Overall (OVR), Posição, Clube, Liga, Nacionalidade, Idade, URL da Foto, e ID Interno do EA FC (se disponível na página).

  * **Estratégia:** Criar um script que navegue pela paginação do SoFIFA ordenado pelo *Overall* (do maior para o menor) e salve os dados em um banco SQLite ou arquivo JSON/CSV para uso rápido.

  * **Tratamento de Erros:** Implementar pausas (sleep) entre as requisições para evitar bloqueio de IP (*rate limiting*).

* **Testes:**

  * *Teste de Extração:* Verificar se os seletores CSS estão capturando corretamente os dados em 10 páginas de amostra.

  * *Teste de Integridade de Dados:* Garantir que nenhum jogador tenha *Overall* vazio ou formato incorreto.

  * *Teste de Duplicidade:* Validar que nenhum ID de jogador se repete no banco final.

## Fase 2: Motor de Validação de Regras e Ordem do Draft

**Objetivo:** Garantir que nenhuma escolha de draft viole as regras customizadas da liga estabelecidas antes do início.

* **Planejamento:**

  * Criar um módulo de validação (ex: `RuleValidator`) que recebe a escolha atual e o elenco atual do usuário.

  * **Regras Suportadas no MVP:**

    * Máximo de jogadores do mesmo clube.

    * Sorteio da ordem do draft e fluxo correto da fila de escolhas.

    * Máximo de jogadores da mesma liga.

    * Máximo de jogadores da mesma nacionalidade.

    * Capacidade máxima do elenco (ex: 23 jogadores).

  * Se a regra for quebrada, a função deve retornar `False` acompanhado de uma mensagem de erro clara (ex: "Limite de 3 jogadores do Real Madrid atingido").

* **Testes (Unitários):**

  * *Cenário Positivo:* Usuário escolhe um jogador que não quebra nenhuma regra -> Retorna `True`.

  * *Cenário Negativo (Limite):* Tentar adicionar o 4º jogador de uma liga cujo limite é 3 -> Retorna `False`.

  * *Cenário de Conflito:* Tentar draftar um jogador que já foi escolhido por outro time -> Retorna `False`.

## Fase 3: Algoritmo de Fallback (Auto-Draft)

**Objetivo:** Manter o andamento do draft caso o tempo (*pick clock*) de um usuário acabe.

* **Planejamento:**

  * Criar um temporizador no backend (ou no loop de eventos principal do MVP local).

  * Quando o tempo zera, o sistema chama a função `AutoDraft`.

  * **Lógica do Auto-Draft:** O sistema deve buscar na lista de jogadores disponíveis, ordenados do maior *Overall* para o menor, e tentar draftar o primeiro jogador. O sistema **deve** passar esse jogador pelo Motor de Validação (Fase 2). Se falhar (regras estouradas), tenta o segundo maior *Overall*, e assim por diante.

* **Testes:**

  * *Teste de Simulação de Timeout:* Forçar o fim do tempo e verificar se o sistema atribui o melhor jogador livre.

  * *Teste de Restrição:* Encher um time de atacantes e verificar se o Auto-Draft respeita os limites (se aplicável) e busca o próximo jogador válido de outra posição/liga.

    * O algoritmo limita cada time a 4 atacantes(ST, CF), 5 pontas (RW, LW, RM, LM), 5 meias (CAM, CM, CDM), 4 laterais (RWB, RB, LWB, LB), 4 zagueiros (CB) e 3 goleiros (GK).

      ou

    * O Algoritmo esboça o time titular até o momento de cada jogador e escolhe o de maior overall de uma posição vaga.

## Fase 4: Frontend (Modo "Comissário" Local)

**Objetivo:** Interface gráfica onde uma pessoa controlará o draft e transmitirá a tela (via Discord, Google Meet, etc.) para os amigos.

* **Planejamento:**

  * **Tecnologia:** React.js, Vue.js ou até mesmo Vanilla JS (se quiser manter muito simples).

  * **Layout:**

    * *Painel Central:* Lista de jogadores disponíveis com barra de pesquisa (por nome, liga, etc.) e filtros.

    * *Sidebar:* O relógio (*pick clock*) e o registro (histórico) das últimas escolhas.

    * *Abas Inferiores/Superiores:* Visão dos elencos em formação de cada participante.

  * O "comissário" terá um botão "Draftar para o Time X" ao lado de cada jogador.

* **Testes (Usabilidade e Estado):**

  * *Teste de Renderização:* Filtrar a lista de jogadores e garantir que a interface responde rápido (sem lentidão com o banco de dados carregado na memória).

  * *Teste de Renderização:* Garantir que o relógio resete ao tempo pré determinado para cada escolha e que a ordem das escolhas seja respeitada, de 1-16 na primeira rodada, 16-1 na segunda rodada e assim por diante.

  * *Teste de UI/UX:* Garantir que, ao draftar, o jogador desapareça instantaneamente da lista de "disponíveis" e apareça na lista do "time".

  * Teste de UI/UX: Garantir que quando o limite máximo de jogadores for atingido, os jogadores ficam bloqueados com o botão de escolher apagado ou com a mensagem “bloqueado“ para não serem escolhidos.

## Fase 5: Exportação para o Jogo (PC Modding)

**Objetivo:** Levar os times criados na plataforma web para dentro do EA FC (PC) de forma automatizada ou semi-automatizada.

* **Planejamento:**

  * **A Ferramenta:** Utilizar softwares da comunidade como o **RDBM (Revolution DataBase Master)** ou o **FIFA Editor Tool**. Eles permitem abrir o arquivo de `Squads` gerado pelo jogo e exportar as tabelas internas para Excel/CSV.

  * **O Processo:**

    1. O usuário cria um arquivo de save `Squads` limpo (com as transferências mais recentes da EA) e o extrai.

    2. Nossa plataforma, após o fim do draft, exporta um script ou um arquivo CSV especificamente formatado.

    3. Esse CSV altera as colunas de `teamid` (ID do Clube) na tabela de jogadores, movendo os jogadores draftados para times genéricos (ex: transferir o time do "João" para o emblema do 'Resto do Mundo - Soccer Aid', ou substituir elencos de times de ligas menores).

    4. O usuário importa esse CSV modificado de volta no RDBM, salva o novo arquivo de `Squads` e o coloca na pasta `Documents/FC 26/settings`.

* **Testes (Práticos no Jogo):**

  * *Teste de Injeção:* Injetar as transferências via RDBM e verificar se o save carrega sem corromper.

  * *Teste de Partida:* Escolher os times draftados no modo *Kick-Off* (Amistoso) e jogar uma partida para garantir que não há quebra de texturas ou *crashes* por falta de dados.

## Fase 6: Comunicação Bidirecional (O Futuro Multiplayer)

**Objetivo:** Permitir que cada usuário entre na plataforma do seu próprio navegador e faça suas escolhas simultaneamente, com atualizações em tempo real.

* **Planejamento:**

  * **Tecnologia:** Implementar `Socket.io` (Node.js) ou `WebSockets` nativos.

  * Mudar a arquitetura de "Comissário Único" para um sistema de Salas (Rooms), onde um usuário cria o Draft, define a senha, e os outros entram.

  * O servidor passa a ser a única fonte da verdade, transmitindo eventos como `player_drafted` e `clock_tick` para todos os clientes conectados.

* **Testes:**

  * *Teste de Concorrência (Race Condition):* Simular dois usuários clicando no mesmo jogador no exato mesmo milissegundo. O backend deve atribuir ao primeiro que chegar e enviar uma mensagem de erro ao segundo.

  * *Fluxo de draft:* Garantir que o relógio resete ao tempo pré determinado para cada escolha e que a ordem das escolhas seja respeitada, de 1-16 na primeira rodada, 16-1 na segunda rodada e assim por diante.

  * *Teste de Reconexão:* Desligar a internet de um cliente, reconectar, e verificar se ele recebe o estado atualizado do draft automaticamente.