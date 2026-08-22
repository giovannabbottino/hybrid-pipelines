# Fluxograma do processo de criação do grafo de conhecimento

Este documento apresenta o fluxo do endpoint `POST /analyze`. O primeiro
diagrama resume o processo em cinco macroblocos; os diagramas seguintes abrem
cada um deles em um nível operacional.

## Visão de alto nível

```mermaid
flowchart LR
    A[1. Receber e validar entrada] --> B[2. Extrair menções]
    B --> C[3. Enriquecer com Wikidata]
    C --> D[4. Criar e validar RDF]
    D --> E[5. Entregar resultado]
```

## 1. Receber e validar entrada

```mermaid
flowchart TD
    A[Receber POST /analyze] --> B[Interpretar corpo JSON]
    B --> C{O campo text é válido?}
    C -- Não --> D[Retornar HTTP 400]
    C -- Sim --> E[Remover espaços nas extremidades]
    E --> F[Definir chave de idempotência e prazo]
    F --> G[Registrar início da análise]
```

**Saída do bloco:** texto normalizado e parâmetros de execução.

## 2. Extrair menções

```mermaid
flowchart TD
    A[Carregar prompts] --> B[Enviar texto ao Ollama]
    B --> C[Interpretar o JSON retornado]
    C --> D[Realinhar ou recuperar menções]
    D --> E[Complementar padrões específicos]
    E --> F[Deduplicar menções]
    F --> G[Aplicar limite de menções]
```

**Saída do bloco:** lista de menções de entidades e conceitos encontradas no
texto.

## 3. Enriquecer com Wikidata

```mermaid
flowchart TD
    A[Pesquisar cada menção] --> B[Consultar Wikidata MCP]
    B --> C{Fallback está habilitado?}
    C -- Sim --> D[Mesclar candidatos da Action API]
    C -- Não --> E[Usar candidatos do MCP]
    D --> F[Selecionar candidato pelo contexto]
    E --> F
    F --> G[Obter declarações da entidade]
    G --> H[Manter relações diretas entre entidades resolvidas]
```

**Saída do bloco:** entidades identificadas, suas declarações e relações
diretas fundamentadas na Wikidata.

## 4. Criar e validar RDF

```mermaid
flowchart TD
    A{RDF determinístico foi solicitado?}
    A -- Sim --> B[Gerar Turtle localmente]
    A -- Não --> C[Montar payload com texto, entidades e relações]
    C --> D[Carregar prompt e solicitar RDF ao Ollama]
    D --> E[Remover cercas de código e notas]
    B --> F{Turtle é válido?}
    E --> F
    F -- Sim --> G[Garantir rótulos e concluir RDF]
    F -- Não --> H[Tentar reparos locais ou RDF determinístico]
    H --> I{Ainda há tentativas?}
    I -- Sim --> D
    I -- Não --> J[Retornar erro HTTP 508]
```

**Saída do bloco:** grafo serializado em RDF/Turtle válido ou erro de
validação após o limite de tentativas.

## 5. Entregar resultado

```mermaid
flowchart TD
    A[Montar AnalyzeResponse] --> B[Incluir texto original]
    B --> C[Incluir entidades e relações]
    C --> D[Incluir RDF e atribuição da fonte]
    D --> E[Incluir saída bruta da extração]
    E --> F[Registrar eventos e gerações configurados]
    F --> G[Retornar JSON com HTTP 200]
```

**Saída do bloco:** resposta JSON com o grafo, as evidências utilizadas e os
metadados da análise.

## Observações transversais

- O prazo restante é verificado entre as etapas principais; estouros de tempo
  são convertidos em HTTP 504.
- Falhas de serviços externos são convertidas em HTTP 502.
- Os eventos da análise e as gerações do Ollama são registrados quando os
  respectivos caminhos de log estão configurados.
