"""Export the FHIR-lite evidence model as FHIR R4 resources.

Evidence citations ride on an extension so standard FHIR tooling can still read
the QuestionnaireResponse answers (valueBoolean).
"""
from __future__ import annotations

from typing import Any

from .domain.models import Questionnaire, QuestionnaireItem, QuestionnaireResponse, ResponseItem

EXT = "https://lotusaihealth.example/fhir/StructureDefinition"


def questionnaire_r4(q: Questionnaire) -> dict[str, Any]:
    def item(i: QuestionnaireItem) -> dict[str, Any]:
        out: dict[str, Any] = {"linkId": i.linkId, "text": i.text, "type": i.type}
        ext = []
        if i.operator:
            ext.append({"url": f"{EXT}/logical-operator", "valueCode": i.operator})
        if i.n is not None:
            ext.append({"url": f"{EXT}/min-true", "valueInteger": i.n})
        if i.arithmetic:
            ext.append({"url": f"{EXT}/group-arithmetic-operator", "valueCode": i.arithmetic})
        if i.points is not None:
            ext.append({"url": f"{EXT}/points", "valueInteger": i.points})
        if ext:
            out["extension"] = ext
        if i.item:
            out["item"] = [item(x) for x in i.item]
        return out

    return {"resourceType": "Questionnaire", "id": q.id, "status": "active", "title": q.title,
            "publisher": q.source, "item": [item(i) for i in q.item]}


def response_r4(r: QuestionnaireResponse) -> dict[str, Any]:
    def item(i: ResponseItem) -> dict[str, Any]:
        out: dict[str, Any] = {"linkId": i.linkId, "text": i.text}
        if i.answer is not None:
            ans: dict[str, Any] = {"valueBoolean": i.answer}
            if i.evidence:
                ans["extension"] = [{"url": f"{EXT}/evidence", "extension": [
                    {"url": "document", "valueString": e.doc_id}, {"url": "page", "valueInteger": e.page},
                    {"url": "quote", "valueString": e.quote}, {"url": "confidence", "valueDecimal": e.confidence}]}
                    for e in i.evidence]
            out["answer"] = [ans]
        if i.item:
            out["item"] = [item(x) for x in i.item]
        return out

    return {"resourceType": "QuestionnaireResponse", "questionnaire": f"Questionnaire/{r.questionnaire}",
            "status": r.status, "subject": {"identifier": {"value": r.subject}}, "item": [item(i) for i in r.item]}
