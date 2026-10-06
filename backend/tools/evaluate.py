"""Evaluate the whole submitted sweep. Missing predictions count against coverage."""
import argparse
import json
from pathlib import Path


def relative(actual, expected):
    return abs(actual-expected)/expected if isinstance(actual,(int,float)) and expected and expected>0 else None


def evaluate(packet, truth):
    books={b['id']:b for b in packet['books']}; items={i['id']:i for i in packet['items']}
    true_books=truth.get('books',[]); true_items=truth.get('items',[])
    mapped = [row.get('system_id') for row in true_books+true_items if row.get('system_id')]
    if len(mapped) != len(set(mapped)):
        raise ValueError('Each physical object must map to a unique system ID')
    if any(b.get('legible') and not b.get('title','').strip() for b in true_books):
        raise ValueError('Human-legible ground truth requires hand-read titles')
    if any(not i.get('category','').strip() for i in true_items):
        raise ValueError('Each true non-book object requires a category')
    normalize=lambda t:' '.join(t.casefold().split())
    legible=[b for b in true_books if b.get('legible')]
    correct=sum(normalize(books.get(b.get('system_id'),{}).get('title',''))==normalize(b['title']) for b in legible)
    wrong=sum(bool(books.get(b.get('system_id'),{}).get('title')) and books[b['system_id']].get('id_confidence',0)>=.75 and normalize(books[b['system_id']]['title'])!=normalize(b['title']) for b in legible)
    wrong += sum(bool(b.get("title")) and b.get("id_confidence",0)>=.75 for id,b in books.items() if id not in mapped)
    dimensions=[]; prices=[]
    for b in true_books:
        if b.get('spine_height_cm') and b.get('spine_thickness_cm'):
            prediction=books.get(b.get('system_id'),{})
            dimensions.append(all((err:=relative(prediction.get(k),b[k])) is not None and err<=.15 for k in ('spine_height_cm','spine_thickness_cm')))
        for kind in ('replacement_cost','used_value'):
            checked=b.get(kind)
            if checked and checked.get('amount') is not None:
                predicted=books.get(b.get('system_id'),{}).get(kind,{})
                err=relative(predicted.get('amount'),checked['amount'])
                prices.append(err is not None and err<=.25 and predicted.get('url')==checked.get('url') and checked.get('currency')==packet['sweep']['currency'])
    count_error=relative(len(books),len(true_books))
    results={
        'book_count':{'relative_error':count_error,'pass':count_error is not None and count_error<=.05},
        'titles':{'human_legible':len(legible),'correct':correct,'confidently_wrong':wrong,'pass':bool(legible) and correct/len(legible)>=.7 and wrong/len(legible)<=.03},
        'spines':{'sample':len(dimensions),'within_15_percent':sum(dimensions),'pass':len(dimensions)>=20 and all(dimensions)},
        'prices':{'sample':len(prices),'within_25_percent_same_source':sum(prices),'pass':len(prices)>=15 and all(prices)},
        'non_books':{'true_count':len(true_items),'found_correct_category':sum(normalize(items.get(i.get('system_id'),{}).get('category',''))==normalize(i['category']) for i in true_items)},
    }
    results['non_books']['pass']=bool(true_items) and results['non_books']['found_correct_category']/len(true_items)>=.8
    for key,tolerance in [('floor_area_m2',.1),('wall_area_m2',.15)]:
        error=relative(packet['room'].get(key),truth.get('room',{}).get(key))
        results[key]={'relative_error':error,'pass':error is not None and error<=tolerance}
    latency=packet['sweep'].get('time_to_packet_s')
    results['packet_latency']={'seconds':latency,'pass':isinstance(latency,(int,float)) and latency<300}
    dataset_ok=len(true_books)>=60 and len({b.get('shelving_unit') for b in true_books if b.get('shelving_unit')})>=2 and len(true_items)>=8
    return {'dataset_meets_minimum':dataset_ok,'results':results,'all_pass':dataset_ok and all(r['pass'] for r in results.values()),'interpretation':'All ground-truth objects are scored, including misses. Strict sample rule: each measured spine/price must meet tolerance. No missing sample is a pass.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('packet');parser.add_argument('ground_truth');parser.add_argument('--output')
    args=parser.parse_args();result=evaluate(json.loads(Path(args.packet).read_text()),json.loads(Path(args.ground_truth).read_text()))
    value=json.dumps(result,indent=2);print(value)
    if args.output: Path(args.output).write_text(value)
