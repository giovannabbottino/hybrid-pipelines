# Fluxograma do processo de criação do grafo de conhecimento

Este documento apresenta o fluxo do endpoint `POST /analyze`. O primeiro
diagrama resume o processo em cinco macroblocos; os diagramas seguintes abrem
cada um deles em nível operacional.

## Visão de alto nível

```mermaid
flowchart LR
    A[1. Receber e validar entrada] --> B[2. Extrair menções]
    B --> C[3. Enriquecer com Wikidata MCP]
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
    B --> C[Interpretar estritamente o JSON retornado]
    C --> D{Há menções válidas no texto?}
    D -- Não --> E[Encerrar com erro]
    D -- Sim --> F[Realinhar menções]
    F --> G[Complementar padrões específicos]
    G --> H[Deduplicar e aplicar o limite]
```

**Saída do bloco:** lista validada de menções de entidades e conceitos. Não há
extração heurística substituta quando a resposta do modelo é inválida ou vazia.

## 3. Enriquecer com Wikidata

```mermaid
flowchart TD
    A[Pesquisar cada menção] --> B[Chamar search_items no Wikidata MCP]
    B --> C[Chamar get_statements para cada candidato]
    C --> D[Construir caminhos entre grupos com até dois saltos]
    D --> E[Excluir intermediários de alto grau]
    E --> F[LLM seleciona um QID fornecido por grupo não vazio]
    F --> G{Seleções válidas e completas?}
    G -- Sim --> H[Manter relações diretas entre entidades selecionadas]
    G -- Não --> I{Restam tentativas de desambiguação?}
    I -- Sim --> J[Reenviar somente grupos pendentes ao LLM]
    J --> F
    I -- Não --> K[Retornar HTTP 422]
```

**Saída do bloco:** entidades identificadas, suas declarações e relações
diretas fundamentadas na Wikidata. O MCP é a única fonte de evidência; sua
indisponibilidade encerra a requisição.

## 4. Criar e validar RDF

```mermaid
flowchart TD
    A[Montar payload com texto, entidades e relações]
    A --> B[Solicitar triplas JSON ao Ollama com schema]
    B --> C[Validar campos, identificadores e metadados de literais]
    C --> D[Normalizar nomes kg e construir grafo RDFLib]
    D --> E{Serialização Turtle válida?}
    E -- Sim --> F[Aplicar pós-condições de IDs e rótulos e revalidar]
    E -- Não --> G{Ainda há tentativas?}
    G -- Sim --> I[Adicionar resposta anterior e erro ao prompt]
    I --> B
    G -- Não --> H[Retornar erro HTTP 422]
```

**Saída do bloco:** grafo serializado em RDF/Turtle válido ou erro explícito.
Uma falha não troca o mecanismo escolhido nem aciona reparos locais.

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
