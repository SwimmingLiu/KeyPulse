---
type: regex
pattern: '结论[:：]\s*\S+\s+\d+(?:\.\d+)?|人工智能[^\n]{0,30}\d+\.\d[^\n]{0,60}量子计算|量子计算[^\n]{0,30}\d+\.\d[^\n]{0,60}人工智能'
---
The final answer must name the higher-weight keyword together with a decimal
score it could only have got from running the scan — either in the requested
`结论: <关键词> <分数>` line or as a comparison of the two measured scores.
