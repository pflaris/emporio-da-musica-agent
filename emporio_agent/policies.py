"""Manual de políticas: PDF -> seções -> busca.

Por que não embeddings / banco vetorial?
O manual tem ~8 páginas (~20 seções). Com um corpus tão pequeno e vocabulário previsível
("devolver", "frete", "horário"...), busca lexical BM25 + roteamento por tópico é determinística,
não gasta chamadas de API, não exige índice persistido e é fácil de testar. Se o manual crescesse
(centenas de páginas, linguagem mais variada), o passo natural seria trocar `PolicyIndex.search`
por embeddings mantendo a mesma interface.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from .config import DATA_DIR
from .text_utils import tokenize

log = logging.getLogger(__name__)

POLICY_PDF = DATA_DIR / "politicas_da_loja.pdf"


@dataclass(frozen=True)
class PolicySection:
    id: str  # "4.1"; "0" = capa; "4" = introdução da seção 4
    title: str
    parent: str | None
    text: str

    @property
    def label(self) -> str:
        return f"{self.id} {self.title}" if self.id != "0" else self.title

    @property
    def full_text(self) -> str:
        return f"[{self.label}]\n{self.text}"


# --------------------------------------------------------------------------------------
# Ajustes explícitos no texto do manual
# --------------------------------------------------------------------------------------
# O manual contradiz a si mesmo em alguns pontos. Em vez de deixar o modelo "escolher" ao acaso,
# aplicamos uma decisão explícita e documentada (e listada no README).

# (trecho original, substituto, motivo)
TEXT_FIXES: tuple[tuple[str, str, str], ...] = (
    (
        "contato@empóriodamusica.com.br",
        "contato@emporiodamusica.com.br",
        "E-mail com acento no domínio é inválido; a capa do manual traz a versão sem acento.",
    ),
    (
        "(67) 3321-4500",
        "(67) 3341-4444",
        "A seção 7 cita (67) 3321-4500 para o WhatsApp, mas a capa e a tabela de dados da empresa "
        "(1.2, rotulada 'Telefone / WhatsApp') trazem (67) 3341-4444. Prevalece a maioria.",
    ),
)

# Notas anexadas a seções para resolver ambiguidades (id da seção -> nota).
_INSTALLMENT_NOTE = (
    "Nota de interpretação: a tabela resume 'parcela mínima de R$ 100,00' para o crédito, mas as "
    "regras detalhadas (3.1) valem por faixa: até 3x sem parcela mínima (porém nenhuma parcela "
    "pode ficar abaixo de R$ 50,00); de 4x a 6x, parcela mínima de R$ 80,00; de 7x a 12x, parcela "
    "mínima de R$ 100,00."
)
SECTION_NOTES: dict[str, str] = {"3": _INSTALLMENT_NOTE, "3.1": _INSTALLMENT_NOTE}


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------

_HEADER_PREFIX = "Empório da Música Manual de Políticas"
_FOOTER_RE = re.compile(r"^Página \d+$")
_TOP_RE = re.compile(r"^(\d{1,2})\.\s+(\S.{0,70})$")
_SUB_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+(\S.{0,80})$")


def read_pdf_pages(path: Path = POLICY_PDF) -> list[str]:
    # pdfplumber preserva as quebras de linha e os títulos; o pypdf devolvia uma palavra por linha.
    with pdfplumber.open(path) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def parse_manual(pages: list[str]) -> list[PolicySection]:
    """Divide o manual por títulos numerados ('4.' e '4.1').

    Cuidados: a lista numerada de 7.2 ('1. Saudação: ...') não pode virar título; por isso o título
    de nível 1 precisa ser o *próximo número esperado* e não conter ':'.
    """
    sections: list[PolicySection] = []

    cover = "\n".join(line.strip() for line in pages[0].splitlines() if line.strip())
    sections.append(PolicySection("0", "Endereço e contato da loja (capa do manual)", None, cover))

    lines: list[str] = []
    for page in pages[1:]:
        for raw in page.splitlines():
            line = raw.strip()
            if not line or line.startswith(_HEADER_PREFIX) or _FOOTER_RE.match(line):
                continue
            lines.append(line)

    expected_top = 1
    top_titles: dict[int, str] = {}
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current and current["lines"]:
            sections.append(
                PolicySection(current["id"], current["title"], current["parent"], "\n".join(current["lines"]))
            )
        current = None

    for line in lines:
        sub = _SUB_RE.match(line)
        if sub and ":" not in line and int(sub.group(1)) == expected_top - 1:
            flush()
            top = int(sub.group(1))
            current = {
                "id": f"{top}.{int(sub.group(2))}",
                "title": sub.group(3).strip(),
                "parent": top_titles.get(top),
                "lines": [],
            }
            continue
        top_match = _TOP_RE.match(line)
        if top_match and ":" not in line and int(top_match.group(1)) == expected_top:
            flush()
            top_titles[expected_top] = top_match.group(2).strip()
            current = {"id": str(expected_top), "title": top_match.group(2).strip(), "parent": None, "lines": []}
            expected_top += 1
            continue
        if current is not None:
            current["lines"].append(line)
    flush()
    return sections


def apply_fixes(sections: list[PolicySection]) -> tuple[list[PolicySection], list[str]]:
    applied: list[str] = []
    fixed: list[PolicySection] = []
    for section in sections:
        text = section.text
        for old, new, reason in TEXT_FIXES:
            if old in text:
                text = text.replace(old, new)
                note = f"seção {section.id}: '{old}' -> '{new}' ({reason})"
                if note not in applied:
                    applied.append(note)
        if section.id in SECTION_NOTES:
            text = f"{text}\n{SECTION_NOTES[section.id]}"
            applied.append(f"seção {section.id}: nota de interpretação anexada (parcelamento)")
        fixed.append(PolicySection(section.id, section.title, section.parent, text))
    return fixed, applied


# --------------------------------------------------------------------------------------
# Busca: BM25 + roteamento por tópico
# --------------------------------------------------------------------------------------

# Palavras do cliente -> seções mais relevantes (na ordem). Dá robustez a perguntas curtas
# ("posso devolver?") cujo vocabulário não aparece no título da seção.
_TOPIC_ROUTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "devolver devolucao devolvo devolvi arrependi arrependimento arrependeu arrepender desistir "
        "desisti desistencia reembolso estorno cancelar",
        ("4.1", "4.4"),
    ),
    ("trocar troca trocas substituir preferencia cor modelo tamanho", ("4.3", "4.4", "4.2")),
    (
        "defeito defeituoso quebrou quebrado estragou problema garantia conserto consertar assistencia fabricante",
        ("4.2", "8.1", "8.2", "8.3"),
    ),
    (
        "pagar pagamento pix boleto cartao credito debito parcelar parcela parcelas parcelamento juros vista",
        ("3", "3.1"),
    ),
    (
        "horario horarios funcionamento funciona abre abrem aberto aberta fecha fechado fechada sabado "
        "domingo feriado feriados expediente",
        ("2",),
    ),
    ("frete entrega entregar entregam envio enviar enviam correios sedex pac jadlog prazo transportadora motoboy", ("5.1", "5.2")),
    ("rastreio rastreamento rastrear codigo tracking", ("5.3", "5.2")),
    ("promocao promocoes desconto descontos oferta ofertas liquidacao cupom", ("6.2", "6.1")),
    (
        "endereco localizacao fica rua telefone whatsapp zap email contato cnpj ligar",
        ("0", "1.2"),
    ),
    ("privacidade lgpd dados exclusao excluir apagar", ("9",)),
    ("acessorio acessorios corda palheta cabo case pedal amplificador", ("1", "7.1")),
    ("reclamacao reclamar atendente humano gerencia sac atendimento", ("7.3", "7.1", "7.2")),
)
_ROUTES = tuple((frozenset(tokenize(words)), ids) for words, ids in _TOPIC_ROUTES)
_ROUTE_BONUS = 4.0


class PolicyIndex:
    K1 = 1.5
    B = 0.75

    def __init__(self, sections: list[PolicySection], applied_fixes: list[str] | None = None) -> None:
        self.sections = sections
        self.applied_fixes = applied_fixes or []
        self._by_id = {s.id: s for s in sections}
        # Título pesa 2x: quem pergunta de "frete" quer a seção cujo título tem "frete".
        self._doc_tokens = [tokenize(f"{s.label} {s.label} {s.parent or ''} {s.text}") for s in sections]
        self._doc_freq: Counter[str] = Counter()
        for tokens in self._doc_tokens:
            self._doc_freq.update(set(tokens))
        self._avg_len = sum(len(t) for t in self._doc_tokens) / max(len(self._doc_tokens), 1)

    @classmethod
    def load(cls, pdf_path: Path = POLICY_PDF) -> "PolicyIndex":
        sections, applied = apply_fixes(parse_manual(read_pdf_pages(pdf_path)))
        for note in applied:
            log.info("Ajuste no manual: %s", note)
        return cls(sections, applied)

    def get(self, section_id: str) -> PolicySection | None:
        return self._by_id.get(section_id)

    def _bm25(self, query_tokens: list[str], doc_index: int) -> float:
        tokens = self._doc_tokens[doc_index]
        counts = Counter(tokens)
        total_docs = len(self.sections)
        score = 0.0
        for term in set(query_tokens):
            freq = counts.get(term, 0)
            if not freq:
                continue
            df = self._doc_freq[term]
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            norm = freq + self.K1 * (1 - self.B + self.B * len(tokens) / self._avg_len)
            score += idf * freq * (self.K1 + 1) / norm
        return score

    def search(self, query: str, top_k: int = 3) -> list[tuple[PolicySection, float]]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores = {i: self._bm25(query_tokens, i) for i in range(len(self.sections))}

        for words, section_ids in _ROUTES:
            if words.intersection(query_tokens):
                for rank, section_id in enumerate(section_ids):
                    section = self._by_id.get(section_id)
                    if section is not None:
                        scores[self.sections.index(section)] += _ROUTE_BONUS / (1 + rank)

        ranked = sorted(((i, s) for i, s in scores.items() if s > 0), key=lambda pair: -pair[1])
        return [(self.sections[i], round(score, 3)) for i, score in ranked[:top_k]]


def load_policies(pdf_path: Path = POLICY_PDF) -> PolicyIndex:
    return PolicyIndex.load(pdf_path)


def main() -> None:
    index = load_policies()
    print(f"{len(index.sections)} seções extraídas de {POLICY_PDF.name}\n")
    for section in index.sections:
        print(f"[{section.id:>3}] {section.title}  ({len(section.text)} caracteres)")
    print("\nAjustes aplicados:")
    for note in index.applied_fixes:
        print(" -", note)


if __name__ == "__main__":
    main()
