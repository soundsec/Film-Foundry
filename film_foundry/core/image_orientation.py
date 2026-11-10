"""Lossless quarter-turn pixel permutation, independent of material formation."""
import numpy as np


def rotate_output(image,quarters=0):
    if type(quarters) is not int or not 0<=quarters<=3:raise ValueError('Output rotation must be 0..3 clockwise quarter turns')
    return np.rot90(image,-quarters) if quarters else image
