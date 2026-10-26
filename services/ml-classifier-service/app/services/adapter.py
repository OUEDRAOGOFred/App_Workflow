"""Local TF-IDF centroid model; MODEL_PATH may supply a JSON training corpus."""

import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

CORPUS = [
    (
        "facture",
        (
            "Facture numéro montant total TVA euros paiement date "
            "fournisseur facture acquittée"
        ),
    ),
    ("facture", "Invoice bill total amount tax payment due bank account supplier"),
    (
        "facture",
        "Facturation achat produits quantité prix unitaire hors taxes TTC règlement",
    ),
    (
        "contrat",
        (
            "Contrat accord entre les parties obligations durée clause "
            "signature résiliation"
        ),
    ),
    ("contrat", "Contract agreement parties terms obligations signature termination"),
    (
        "contrat",
        (
            "Convention bail prestation employeur salarié engagement "
            "juridique durée signataires"
        ),
    ),
    (
        "cv",
        (
            "Curriculum vitae CV expérience professionnelle compétences "
            "formation diplôme candidature"
        ),
    ),
    (
        "cv",
        "Resume curriculum vitae skills education work experience employment candidate",
    ),
    (
        "cv",
        (
            "Parcours professionnel stages université langues "
            "certifications développeur contact"
        ),
    ),
]


def words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]{2,}", normalized)


class DemoAdapter:
    def __init__(self, labels: list[str], model_path: str = ""):
        self.labels = labels
        data = json.loads(Path(model_path).read_text()) if model_path else CORPUS
        if (
            not isinstance(data, list)
            or not data
            or any(
                not isinstance(row, (list, tuple))
                or len(row) != 2
                or not all(isinstance(x, str) for x in row)
                for row in data
            )
        ):
            raise ValueError("MODEL_PATH doit contenir des paires JSON [classe, texte]")
        documents = [
            (label, Counter(words(text))) for label, text in data if label in labels
        ]
        vocabulary = (
            set().union(*(set(tokens) for _, tokens in documents))
            if documents
            else set()
        )
        self.idf = {
            term: math.log(
                (1 + len(documents))
                / (1 + sum(term in tokens for _, tokens in documents))
            )
            + 1
            for term in vocabulary
        }
        self.centroids = {label: Counter() for label in labels}
        for label, tokens in documents:
            self.centroids[label].update(self.vector(tokens))

    def vector(self, tokens: Counter) -> dict:
        return {
            term: count * self.idf[term]
            for term, count in tokens.items()
            if term in self.idf
        }

    def predict(self, text: str) -> tuple[str, float, dict[str, float]]:
        vector = self.vector(Counter(words(text)))
        norm = math.sqrt(sum(value * value for value in vector.values()))
        similarities = {}
        for label, centroid in self.centroids.items():
            length = math.sqrt(sum(value * value for value in centroid.values()))
            similarities[label] = (
                sum(value * centroid.get(term, 0) for term, value in vector.items())
                / (norm * length)
                if norm and length
                else 0
            )
        total = sum(similarities.values())
        scores = {
            label: round(value / total, 6) if total else 0.0
            for label, value in similarities.items()
        }
        label = max(scores, key=scores.get)
        return (label, scores[label], scores) if total else ("inconnu", 0.0, scores)
