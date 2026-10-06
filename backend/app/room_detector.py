"""Explicit open-vocabulary room categories, including structural negative classes."""
ROOM_CLASSES = ['book', 'bookshelf', 'cabinet', 'chair', 'sofa', 'table', 'coffee machine',
                'lamp', 'framed painting', 'portrait', 'rug', 'television', 'monitor',
                'laptop', 'speaker', 'vase', 'clock', 'mirror', 'plant pot', 'curtain',
                'air conditioner', 'fan', 'printer', 'sculpture', 'cushion', 'bottle', 'cup',
                'person', 'cell phone', 'door', 'window', 'wall', 'floor', 'paper bag']
IGNORED_CLASSES = {'person', 'door', 'window', 'wall', 'floor', 'paper bag'}
