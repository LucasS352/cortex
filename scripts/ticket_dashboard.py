"""Consistent, one-ticket-one-count projections for the open-ticket dashboard."""
from collections import Counter

import overview_insights as insights


UNKNOWN_COMPANY = "__unidentified__"
AMBIGUOUS_COMPANY = "__ambiguous__"
UNKNOWN_OWNER = "__unassigned__"
INTERNAL_ORGANIZATION_IDS = {"1258969477"}  # Alternativa Sistemas LTDA in Movidesk


def _organizations(clients):
    organizations = {}
    for client in clients if isinstance(clients, list) else []:
        if not isinstance(client, dict):
            continue
        org = client.get("organization") or {}
        if not isinstance(org, dict):
            org = {}
        # Movidesk also places the company itself in clients (personType=2),
        # without a nested organization. Keep the explicit parent when present.
        if not org.get("id") and str(client.get("personType")) == "2":
            org = client
        org_id = str(org.get("id") or "").strip()
        if org_id:
            organizations[org_id] = (org.get("businessName") or organizations.get(org_id)
                                     or f"Organização {org_id}")
    return organizations


def prepare(rows, reference, topic_fn):
    prepared = []
    for source in rows:
        row = dict(source)
        clients = row.pop("clients", None)
        organizations = _organizations(clients)
        if len(organizations) > 1:
            external = {org_id: name for org_id, name in organizations.items()
                        if org_id not in INTERNAL_ORGANIZATION_IDS}
            if external:
                organizations = external
        if len(organizations) > 1:
            company_id, company_name = AMBIGUOUS_COMPANY, "Empresa ambígua"
        elif organizations:
            company_id, company_name = next(iter(organizations.items()))
        else:
            company_id, company_name = UNKNOWN_COMPANY, "Não identificada"
        company_issue = ("no_client" if not clients else "person_without_company") if not organizations else None
        owner = row.pop("owner", None) or {}
        owner_id = str(owner.get("id") or "").strip()
        row.update(company_id=company_id, company_name=company_name,
                   company_issue=company_issue,
                   associated_companies=list(organizations.values()),
                   owner_id=owner_id or UNKNOWN_OWNER,
                   owner_name=(owner.get("businessName") or f"Responsável {owner_id}")
                   if owner_id else "Sem responsável",
                   team=insights.department_for(row.get("owner_team")),
                   responsibility=insights.responsibility_for(row.get("owner_team"), row.get("status")))
        prepared.append(row)
    return insights.enrich(prepared, reference, topic_fn)


def _age_match(days, age):
    if not age:
        return True
    if days is None:
        return age == "unknown"
    return {"0-7": days <= 7, "8-30": 8 <= days <= 30,
            "31-90": 31 <= days <= 90, "91+": days > 90,
            "30+": days > 30}.get(age, False)


def select(rows, filters):
    q = (filters.get("q") or "").casefold().strip()
    result = []
    for row in rows:
        searchable = " ".join(str(v or "") for v in (
            row.get("external_id"), row.get("subject"), row.get("problem_statement"),
            row.get("technical_narrative"), row.get("company_name"),
            row.get("client_business_name"), row.get("owner_name"),
            " ".join(row.get("associated_companies") or [])))
        if q and q not in searchable.casefold():
            continue
        if filters.get("company") and row["company_id"] != filters["company"]:
            continue
        if filters.get("organization") and (row["company_id"] in (UNKNOWN_COMPANY, AMBIGUOUS_COMPANY)
                                            or row["company_name"] != filters["organization"]):
            continue
        if filters.get("owner") and row["owner_id"] != filters["owner"]:
            continue
        if filters.get("team") and row["team"] != filters["team"]:
            continue
        if filters.get("department") and row["responsibility"] != filters["department"]:
            continue
        if filters.get("status") and (row["status"] or "Sem status") != filters["status"]:
            continue
        if filters.get("topic") and row["topic"] != filters["topic"]:
            continue
        if filters.get("queue") and row["queue"] != filters["queue"]:
            continue
        if filters.get("high") and not row["is_urgent"]:
            continue
        if not _age_match(row["age_days"], filters.get("age")):
            continue
        result.append(row)
    return result


def _group(rows, key_field, label_field):
    groups = {}
    for row in rows:
        key = row[key_field]
        if key not in groups:
            groups[key] = {"id": key, "name": row[label_field], "count": 0,
                           "urgent": 0, "older_30d": 0, "_names": Counter()}
        group = groups[key]
        group["_names"][row[label_field]] += 1
        group["count"] += 1
        group["urgent"] += int(row["is_urgent"])
        group["older_30d"] += int(row["age_days"] is not None and row["age_days"] > 30)
    for group in groups.values():
        group["name"] = sorted(group.pop("_names").items(),
                               key=lambda pair: (-pair[1], pair[0].casefold()))[0][0]
    return sorted(groups.values(), key=lambda x: (-x["count"], x["name"].casefold()))


def dashboard(rows, filters, page=1, limit=20):
    selected = select(rows, filters)
    if filters.get("order") == "priority":
        selected.sort(key=insights.priority_key)
    elif filters.get("order") == "age_asc":
        selected.sort(key=lambda r: (r["age_days"] is None,
                                     r["age_days"] if r["age_days"] is not None else 0,
                                     r["external_id"]))
    elif filters.get("order") == "age_desc":
        selected.sort(key=lambda r: (r["age_days"] is None,
                                     -(r["age_days"] if r["age_days"] is not None else 0),
                                     r["external_id"]))
    elif filters.get("order") == "oldest":
        selected.sort(key=lambda r: (r["created_date"] is None, r["created_date"], r["external_id"]))
    else:
        selected.sort(key=lambda r: (r["created_date"] is not None,
                                    r["created_date"], r["external_id"]), reverse=True)
    companies = _group(selected, "company_id", "company_name")
    owners = _group(selected, "owner_id", "owner_name")
    total = len(selected)
    assert sum(x["count"] for x in companies) == total
    assert sum(x["count"] for x in owners) == total
    start = (page - 1) * limit
    items = [{"id": r["external_id"], "subject": r["subject"] or "Sem assunto",
              "company_id": r["company_id"], "company": r["company_name"],
              "company_issue": r["company_issue"],
              "associated_companies": r["associated_companies"],
              "owner_id": r["owner_id"], "owner": r["owner_name"], "team": r["team"],
              "urgency": r["urgency"] or "Normal", "status": r["status"] or "Sem status",
              "topic": r["topic"], "age_days": r["age_days"],
              "created_at": r["created_date"].isoformat() if r["created_date"] else None,
              "summary": (r["problem_statement"] or "")[:500]}
             for r in selected[start:start + limit]]
    return {"items": items, "total": total, "global_total": len(rows), "page": page,
            "limit": limit,
            "kpis": {"companies": sum(g["id"] not in (UNKNOWN_COMPANY, AMBIGUOUS_COMPANY) for g in companies),
                     "owners": sum(g["id"] != UNKNOWN_OWNER for g in owners),
                     "unidentified": next((g["count"] for g in companies if g["id"] == UNKNOWN_COMPANY), 0),
                     "without_client": sum(r["company_issue"] == "no_client" for r in selected),
                     "person_without_company": sum(r["company_issue"] == "person_without_company" for r in selected),
                     "ambiguous": next((g["count"] for g in companies if g["id"] == AMBIGUOUS_COMPANY), 0),
                     "unassigned": next((g["count"] for g in owners if g["id"] == UNKNOWN_OWNER), 0),
                     "urgent": sum(r["is_urgent"] for r in selected)},
            "companies": companies, "owners": owners,
            "facets": {"companies": _group(rows, "company_id", "company_name"),
                       "owners": _group(rows, "owner_id", "owner_name"),
                       "teams": [{"name": name, "count": count} for name, count in sorted(Counter(r["team"] for r in rows).items())],
                       "statuses": [{"name": name, "count": count} for name, count in sorted(Counter(r["status"] or "Sem status" for r in rows).items())],
                       "topics": [{"name": name, "count": count} for name, count in sorted(Counter(r["topic"] for r in rows).items())]}}
