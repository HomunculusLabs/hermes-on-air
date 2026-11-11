"""Synthetic benign stream lines; paired contexts are deliberately ordinary chat.

This is a generated teaching/chat fixture, not an adjudicated live-chat soak.
"""
TOPICS = ('the parser', 'the renderer', 'the overlay', 'the workshop', 'the camera',
          'the texture', 'the shader', 'the model', 'the animation', 'the display',
          'the layout', 'the timeline', 'the notebook', 'the examples', 'the lesson',
          'the palette', 'the keyboard', 'the network', 'the project', 'the preview')
FRAMES = (
    'Let us take a look at {} together.',
    'What do you think about {}?',
    'We can make {} a little simpler.',
    'Thanks for helping us explore {}.',
    'I will show you how {} works.',
    'There is a lot to learn from {}.',
    'The next step is to test {} carefully.',
    'That is a great question about {}.',
    'Today we are experimenting with {}.',
    'We can revisit {} after the break.',
)
LINES = tuple(frame.format(topic) for topic in TOPICS for frame in FRAMES) + (
    'The parser parses into a plain object.',
    'That really means a lot, chat.',
    'They say hello every stream.',
    'The error message parses into a helpful explanation.',
    'Welcome everyone, I hope you are doing well.',
)
