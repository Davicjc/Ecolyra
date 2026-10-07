"""Atualização automática pelo GitHub Releases.

Como funciona (e por que o antivírus não implica):
  • consulta https://api.github.com/repos/<REPO>/releases/latest (HTTPS, só leitura);
  • se a tag (ex.: v2.1.0) for maior que VERSAO, baixa o anexo Ecolyra.zip da release;
  • confere o SHA-256 que o próprio GitHub publica para o anexo;
  • o zip só tem código Python, ícones e textos — nenhum .exe, nenhum script de PowerShell
    codificado, nada é executado de dentro dele. Os arquivos são copiados por cima da pasta
    (config.json e cache/ nunca são tocados) e a versão anterior fica guardada em .versoes/;
  • se o requirements.txt mudou, roda o pip do próprio Python (janela oculta);
  • o app reinicia sozinho com o mesmo pythonw.exe que já estava rodando.

Pasta com .git (a do desenvolvedor) não recebe o zip: faz 'git pull --ff-only' se não houver nada em edição.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import requests

from versao import REPO, VERSAO

PASTA = os.path.dirname(os.path.abspath(__file__))
PROTEGIDOS = {"config.json", "cache", "release", ".git", ".versoes", "__pycache__", "posicao.txt"}
SEM_JANELA = 0x08000000
API = os.environ.get("ECOLYRA_API_RELEASES", f"https://api.github.com/repos/{REPO}/releases/latest")


def numero(tag):
    """'v2.10.1' → (2, 10, 1) — compara versões como números, não como texto."""
    partes = []
    for p in str(tag).strip().lstrip("vV").split("."):
        dig = "".join(c for c in p if c.isdigit())
        partes.append(int(dig) if dig else 0)
    return tuple(partes + [0] * (3 - len(partes)))


def modo_dev(pasta=PASTA):
    return os.path.isdir(os.path.join(pasta, ".git")) and not os.environ.get("ECOLYRA_FORCAR_UPDATE")


def verificar(timeout=10):
    """None se está em dia; senão {'versao', 'notas', 'url', 'digest', 'pagina'}."""
    r = requests.get(API, timeout=timeout, headers={"Accept": "application/vnd.github+json",
                                                    "User-Agent": f"Ecolyra/{VERSAO}"})
    r.raise_for_status()
    rel = r.json()
    tag = rel.get("tag_name") or ""
    if numero(tag) <= numero(VERSAO):
        return None
    anexo = next((a for a in rel.get("assets", []) if a.get("name", "").lower() == "ecolyra.zip"), None)
    if anexo:
        url, digest = anexo["browser_download_url"], anexo.get("digest") or ""
    else:  # release sem anexo: usa o zip do código-fonte da tag
        url, digest = rel.get("zipball_url"), ""
    return {"versao": tag.lstrip("vV"), "notas": rel.get("body") or "", "url": url,
            "digest": digest, "pagina": rel.get("html_url", "")}


def _raiz_do_app(pasta):
    """O zip vem com um prefixo (Ecolyra/ ou Davicjc-ecolyra-abc123/) → acha a pasta com o ecolyra.pyw."""
    for atual, _, arquivos in os.walk(pasta):
        if "ecolyra.pyw" in arquivos and "versao.py" in arquivos:
            return atual
    return None


def _ler(arq):
    try:
        return open(arq, "rb").read()
    except OSError:
        return b""


def aplicar(info, pasta=PASTA, progresso=None):
    """Baixa, confere e instala a versão nova. Devolve a versão instalada (ou lança erro)."""
    avisar = progresso or (lambda t: None)
    if modo_dev(pasta):
        raise RuntimeError("pasta de desenvolvimento (.git) — atualize pelo git")
    tmp = tempfile.mkdtemp(prefix="ecolyra_upd_")
    try:
        avisar(f"Baixando a versão {info['versao']}...")
        zarq = os.path.join(tmp, "Ecolyra.zip")
        sha = hashlib.sha256()
        with requests.get(info["url"], stream=True, timeout=30, headers={"User-Agent": f"Ecolyra/{VERSAO}"}) as r:
            r.raise_for_status()
            with open(zarq, "wb") as f:
                for bloco in r.iter_content(65536):
                    f.write(bloco)
                    sha.update(bloco)
        if info.get("digest", "").startswith("sha256:") and info["digest"][7:].lower() != sha.hexdigest():
            raise RuntimeError("o arquivo baixado não confere com o SHA-256 da release")

        avisar("Conferindo os arquivos...")
        destino_zip = os.path.join(tmp, "x")
        with zipfile.ZipFile(zarq) as z:
            for nome in z.namelist():  # nada de caminho absoluto ou "../" saindo da pasta
                alvo = os.path.realpath(os.path.join(destino_zip, nome))
                if not alvo.startswith(os.path.realpath(destino_zip) + os.sep) and alvo != os.path.realpath(destino_zip):
                    raise RuntimeError(f"caminho suspeito no zip: {nome}")
            z.extractall(destino_zip)
        raiz = _raiz_do_app(destino_zip)
        if not raiz:
            raise RuntimeError("o zip da release não tem o ecolyra.pyw")
        try:
            compile(_ler(os.path.join(raiz, "ecolyra.pyw")), "ecolyra.pyw", "exec")
        except SyntaxError as e:
            raise RuntimeError(f"versão nova com erro de sintaxe ({e}) — mantendo a atual")

        # guarda a versão atual (só o que vai ser substituído) para poder voltar
        backup = os.path.join(pasta, ".versoes", VERSAO)
        req_antes = _ler(os.path.join(pasta, "requirements.txt"))
        avisar("Instalando...")
        trocas = []  # (origem, destino, backup|None) — só o que mudou
        for atual, pastas, arquivos in os.walk(raiz):
            rel = os.path.relpath(atual, raiz)
            if rel.split(os.sep)[0] in PROTEGIDOS:
                pastas[:] = []
                continue
            for nome in arquivos:
                if rel == "." and nome in PROTEGIDOS:
                    continue
                de = os.path.join(atual, nome)
                para = os.path.normpath(os.path.join(pasta, rel, nome))
                if os.path.exists(para) and _ler(para) == _ler(de):
                    continue
                bk = os.path.normpath(os.path.join(backup, rel, nome)) if os.path.exists(para) else None
                trocas.append((de, para, bk))
        # versao.py por último: se algo falhar no meio, a versão continua "velha" e tenta de novo depois
        trocas.sort(key=lambda t: os.path.basename(t[1]) == "versao.py")
        feitas = []
        try:
            for de, para, bk in trocas:  # 1) prepara tudo ao lado (.novo) e guarda o backup
                os.makedirs(os.path.dirname(para), exist_ok=True)
                shutil.copy2(de, para + ".novo")
                if bk:
                    os.makedirs(os.path.dirname(bk), exist_ok=True)
                    shutil.copy2(para, bk)
            for de, para, bk in trocas:  # 2) troca atômica arquivo por arquivo
                os.replace(para + ".novo", para)
                feitas.append((para, bk))
        except Exception:
            for para, bk in reversed(feitas):  # tudo ou nada: volta o que já tinha trocado
                try:
                    shutil.copy2(bk, para) if bk else os.remove(para)
                except OSError:
                    pass
            for _, para, _ in trocas:
                try:
                    os.remove(para + ".novo")
                except OSError:
                    pass
            raise
        if _ler(os.path.join(pasta, "requirements.txt")) != req_antes:
            avisar("Atualizando as bibliotecas...")
            instalar_dependencias(pasta)
        os.makedirs(os.path.join(pasta, "cache"), exist_ok=True)
        json.dump({"de": VERSAO, "para": info["versao"], "quando": time.time()},
                  open(os.path.join(pasta, "cache", "atualizado.json"), "w", encoding="utf-8"))
        return info["versao"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def atualizar_git(pasta=PASTA):
    """Pasta de desenvolvimento: se não tem nada em edição, faz 'git pull --ff-only'.
    Devolve True se chegaram commits novos (aí o app reinicia)."""
    def git(*a):
        return subprocess.run(["git", "-C", pasta, *a], capture_output=True, text=True, timeout=120,
                              creationflags=SEM_JANELA)
    try:
        if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            return False  # tem alteração local: não mexe
        antes = git("rev-parse", "HEAD").stdout.strip()
        if git("pull", "--ff-only", "--quiet").returncode != 0:
            return False
        return git("rev-parse", "HEAD").stdout.strip() != antes
    except Exception:
        return False


def instalar_dependencias(pasta=PASTA):
    py = sys.executable.replace("pythonw.exe", "python.exe")
    subprocess.run([py, "-m", "pip", "install", "--user", "--disable-pip-version-check", "--quiet",
                    "-r", os.path.join(pasta, "requirements.txt")], creationflags=SEM_JANELA, timeout=900)


def reiniciar(pasta=PASTA, extra=()):
    """Abre o Ecolyra de novo (o processo novo espera este liberar a porta) — quem chama fecha este."""
    pyw = sys.executable
    if pyw.lower().endswith("python.exe") and os.path.exists(pyw[:-10] + "pythonw.exe"):
        pyw = pyw[:-10] + "pythonw.exe"
    subprocess.Popen([pyw, os.path.join(pasta, "ecolyra.pyw"), "--reiniciado", *extra], cwd=pasta,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP


def novidades_instaladas(pasta=PASTA):
    """Se acabou de atualizar, devolve {'de','para'} uma vez (para mostrar o aviso)."""
    arq = os.path.join(pasta, "cache", "atualizado.json")
    try:
        d = json.load(open(arq, encoding="utf-8"))
        os.remove(arq)
        return d
    except Exception:
        return None


if __name__ == "__main__":  # python atualizador.py → verifica e atualiza na linha de comando
    info = verificar()
    if not info:
        print(f"Ecolyra {VERSAO} já é a versão mais nova.")
    else:
        print(f"Nova versão {info['versao']} (atual {VERSAO})")
        if "--aplicar" in sys.argv:
            print("instalada:", aplicar(info, progresso=print))
