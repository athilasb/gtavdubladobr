# GTA V Dublado BR

Ferramenta aberta para **dublar o GTA V (PC, Steam/Legacy) em português do Brasil**:
listar e ouvir todas as falas do jogo, gravar ou importar a sua voz, e aplicar no jogo
com backup dos arquivos originais.

> Este repositório **não contém nenhum arquivo do jogo**. Você precisa ter o GTA V instalado.
> Use apenas no modo história.

## Como usar
1. Instale o Python 3.11.
2. Dois cliques em `Abrir Dublador GTA V.bat` (instala as bibliotecas na 1ª vez).
3. Escolha a pasta do GTA V se não for a padrão.
4. Clique numa fala, ESPAÇO para ouvir, R para gravar (ou importe um arquivo).
5. Feche o jogo e clique em **APLICAR NO JOGO**. "Restaurar áudio original" desfaz tudo.

Mais detalhes em [LEIA-ME.txt](LEIA-ME.txt).

## Como funciona
| Arquivo | O que faz |
|---|---|
| `gtav_rpf.py` | Lê e grava arquivos RPF7 (criptografia NG/AES). A chave é lida do seu próprio `GTA5.exe`; as tabelas vêm do `magic.dat` do CodeWalker, baixado automaticamente. |
| `gtav_awc.py` | Lê e grava áudio AWC (IMA ADPCM da Rockstar, RSXXTEA, multicanal). |
| `projeto_v.py` | Índice das ~256 mil falas, substituições, aplicar/restaurar. |
| `dublador_v.py` | Interface (tkinter). |
| `falantes_*.py` | Identifica pela voz quais falas são do Michael, Franklin e Trevor. |

Modos de aplicação:
- **direto**: regrava só as falas trocadas dentro do `.rpf` do jogo (mesma criptografia), com cópia do original em `backup_original`.
- **mods**: grava na pasta `mods` (precisa do OpenIV.asi).

## Como ajudar
- Grave e revise falas, envie traduções e correções.
- Pull requests são bem-vindos (código, tradução, nomes das falas).

## Créditos
Formato RPF/AWC baseado no [CodeWalker](https://github.com/dexyfex/CodeWalker) (MIT).
GTA V é marca da Rockstar Games; este projeto não tem ligação com a Rockstar.
