# Reply classifier eval

**99.6% accuracy on 1,500 replies** (6 wrong), macro F1 0.997. Model `claude-haiku-5-5`, prompt `e7e3dab8d980`. API cost USD 0.1433 (1,049,289 input + 76,648 output tokens).

Interested replies filed under another label: **0** of 441. These are the expensive mistakes: a buyer who said yes and got no follow-up.

## Per class

| Label | Support | Predicted | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| `interested` | 441 | 447 | 98.7% | 100.0% | 0.993 |
| `not_now` | 400 | 394 | 100.0% | 98.5% | 0.992 |
| `out_of_office` | 312 | 312 | 100.0% | 100.0% | 1.000 |
| `referral` | 142 | 142 | 100.0% | 100.0% | 1.000 |
| `unsubscribe` | 205 | 205 | 100.0% | 100.0% | 1.000 |

## Confusion matrix

Rows are the true label, columns the predicted label.

| true \ predicted | `interested` | `not_now` | `out_of_office` | `referral` | `unsubscribe` |
|---|---:|---:|---:|---:|---:|
| `interested` | **441** | · | · | · | · |
| `not_now` | 6 | **394** | · | · | · |
| `out_of_office` | · | · | **312** | · | · |
| `referral` | · | · | · | **142** | · |
| `unsubscribe` | · | · | · | · | **205** |

## Is the confidence score useful?

| Stated confidence | Replies | Accuracy |
|---|---:|---:|
| < 0.70 | 0 |  |
| 0.70-0.89 | 170 | 97.1% |
| 0.90-0.94 | 329 | 99.7% |
| >= 0.95 | 1001 | 100.0% |

## The 6 most instructive mistakes

One per type of confusion first (most common first), the most confident error of each type.

1. **E00150**: true `not_now`, predicted `interested` (confidence 0.90)
   - Subject: Re: Re: your demo request
   - Reply: "Hey,  We're reviewing tools next quarter. Put me down for a follow-up then.  Robert"
   - Model's reason: Sender asks for a follow-up next quarter, showing future intent to move forward with a demo.
2. **E00563**: true `not_now`, predicted `interested` (confidence 0.85)
   - Subject: Re: Ideas for Juniperlight Outfitters
   - Reply: "Good morning,  We're reviewing tools next quarter. Put me down for a follow-up then.  Regards, Theresa Edwards"
   - Model's reason: Sender agrees to a follow-up next quarter, which is a request to continue the conversation.
3. **E00087**: true `not_now`, predicted `interested` (confidence 0.80)
   - Subject: Re: Ideas for Mapleford Fitness
   - Reply: "Good morning,  We're reviewing tools next quarter. Put me down for a follow-up then.  Thanks Frank / Mapleford Fitness"
   - Model's reason: Asks for a follow-up next quarter when they review tools; wants to continue the conversation.
4. **E00210**: true `not_now`, predicted `interested` (confidence 0.80)
   - Subject: Re: Cutting admin time at Fernline Consulting
   - Reply: "Thanks for reaching out.  We're reviewing tools next quarter. Put me down for a follow-up then.  Thanks Gregg / Fernline Consulting"
   - Model's reason: Asks for a follow-up next quarter when they review tools; wants to continue.
5. **E00520**: true `not_now`, predicted `interested` (confidence 0.80)
   - Subject: Re: Cutting admin time at Quillline Gym
   - Reply: "Good morning,  We're reviewing tools next quarter. Put me down for a follow-up then.  Thanks, Joseph"
   - Model's reason: Asks for a follow-up next quarter, so the sender wants to continue the conversation later.
6. **E00758**: true `not_now`, predicted `interested` (confidence 0.80)
   - Subject: Re: Following up
   - Reply: "We're reviewing tools next quarter. Put me down for a follow-up then.  Best, Vincent"
   - Model's reason: Asks for a follow-up next quarter, showing future interest in evaluating tools.
