---
type: regex
pattern: '(结论[:：]\s*\S+\s*\d+(?:\.\d+)?|(人工智能|量子计算)[^\n]{0,40}\d+(?:\.\d+)?|\d+(?:\.\d+)?[^\n]{0,40}(人工智能|量子计算))'
---
The final answer must name one of the two keywords together with a numeric
score — a number it could only have got from running the scan. It may use the
requested `结论: <关键词> <分数>` line or say it in a sentence.
