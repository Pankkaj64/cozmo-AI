"""Observed latency summaries; missing measurements are never reported as zero."""
from collections import defaultdict


def performance_summary(packet):
    samples = defaultdict(list)
    for run in packet.get('stage_runs', []):
        if isinstance(run.get('elapsed_s'), (int, float)):
            samples[run['name']].append(run['elapsed_s'])
    for frame in packet.get('frames', []):
        for run in frame.get('pipeline_stages', []):
            if isinstance(run.get('elapsed_s'), (int, float)):
                samples[run['name']].append(run['elapsed_s'])
    stages = {name: {'calls': len(values), 'total_s': round(sum(values), 3),
                     'mean_s': round(sum(values)/len(values), 3), 'max_s': max(values)}
              for name, values in samples.items()}
    return {'stages': stages, 'time_to_packet_s': packet['sweep'].get('time_to_packet_s'),
            'cost': packet.get('cost', {}),
            'note': 'Measured operations only. Concurrent stages must not be summed as end-to-end latency.'}
