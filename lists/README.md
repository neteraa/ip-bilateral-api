# Listas externas de sancionados

Coloque aqui os arquivos CSV de listas de sancionados.

## Formato esperado

Cada arquivo CSV deve ter uma coluna `document` com CPF (11 dígitos) ou CNPJ (14 dígitos) **sem pontuação**:

```csv
document,name,reason
12345678901,Fulano de Tal,Tráfico
00000000000191,Empresa X LTDA,Lavagem de dinheiro
```

## Arquivos suportados

| Arquivo | Fonte sugerida |
|---|---|
| `ofac.csv` | OFAC SDN List (EUA — Office of Foreign Assets Control) |
| `coaf.csv` | COAF (Conselho de Controle de Atividades Financeiras — Brasil) |

## Atualização

As listas são carregadas na inicialização do servidor. Para recarregar sem restart:
```
POST /admin/reload-lists   (a implementar se necessário)
```

> ⚠️ **Não commitar** listas com dados reais — adicionar ao `.gitignore` se necessário.
