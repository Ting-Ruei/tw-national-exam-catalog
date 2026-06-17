# Source Policy

## Primary Source

The primary source is the MOEX exam question-and-answer search platform:

```text
https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx
```

The page exposes stable identifiers in HTML checkbox IDs and PDF URLs.

Category checkbox:

```text
ctl00_holderContent_chk_{exam_code}_{category_code}
```

Subject checkbox:

```text
ctl00_holderContent_chk_{exam_code}_{category_code}_{subject_code}
```

PDF URL:

```text
wHandExamQandA_File.ashx?t=Q|S|M&code={exam_code}&c={category_code}&s={subject_code}&q={question_set}
```

## Document Roles

- `Q`: question PDF
- `S`: answer PDF
- `M`: correction answer PDF

## Preservation Rules

- Preserve official raw names and labels.
- Add normalized names as derived fields.
- Do not silently merge public-service categories into professional license
  categories.
- Treat missing answer links as metadata, not automatic failure.
- Record parser fixes and manual overrides explicitly.

