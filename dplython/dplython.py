# Chris Riederer
# 2016-02-17

"""Dplyr-style operations on top of pandas DataFrame."""
from __future__ import absolute_import

import itertools
import operator
import sys
import types

import numpy as np
import pandas
from pandas import DataFrame
import six
from six.moves import filter
from six.moves import range
from six.moves import zip


__version__ = "0.0.1"


# TODOs:
# add len to Later

# * Descending and ascending for arrange
# * diamonds >> select(-X.cut)
# * Move special function Later code into Later object
# * Add more tests
# * Reflection thing in Later -- understand this better
# * Should rename some things to be clearer. "df" isn't really a df in the 
    # __radd__ code, for example 
# * lint
# * Let users use strings instead of Laters in certain situations
#     e.g. select("cut", "carat")
# * What about implementing Manager as a container as well? This would help
#     with situations where column names have spaces. X["type of horse"]
# * Should I enforce that output is a dataframe?
#     For example, should df >> (lambda x: 7) be allowed?
# * Pass args, kwargs into sample

# Scratch
# https://mtomassoli.wordpress.com/2012/03/18/currying-in-python/
# http://stackoverflow.com/questions/16372229/how-to-catch-any-method-called-on-an-object-in-python
# Sort of define your own operators: http://code.activestate.com/recipes/384122/
# http://pandas.pydata.org/pandas-docs/stable/internals.html
# I think it might be possible to override __rrshift__ and possibly leave 
#   the pandas dataframe entirely alone.
# http://www.rafekettler.com/magicmethods.html


class Manager(object):
  """Object which helps create a delayed computational unit.

  Typically will be set as a global variable X.
  X.foo will refer to the "foo" column of the DataFrame in which it is later
  applied. 

  Manager can be used in two ways: 
  (1) attribute notation: X.foo
  (2) item notation: X["foo"]

  Attribute notation is preferred but item notation can be used in cases where 
  column names contain characters on which python will choke, such as spaces, 
  periods, and so forth.
  """
  def __getattr__(self, attr):
    return Later(attr)

  def __getitem__(self, key):
    return Later(key)


X = Manager()


reversible_operators = [
    ["__add__", "__radd__"],
    ["__sub__", "__rsub__"],
    ["__mul__", "__rmul__"],
    ["__floordiv__", "__rfloordiv__"],
    ["__div__", "__rdiv__"],
    ["__truediv__", "__rtruediv__"],
    ["__mod__", "__rmod__"],
    ["__divmod__", "__rdivmod__"],
    ["__pow__", "__rpow__"],
    ["__lshift__", "__rlshift__"],
    ["__rshift__", "__rrshift__"],
    ["__and__", "__rand__"],
    ["__or__", "__ror__"],
    ["__xor__", "__rxor__"],
]

normal_operators = [
    "__abs__", "__concat__", "__contains__", "__delitem__", "__delslice__",
    "__eq__", "__file__", "__ge__", "__getitem__", "__getslice__", "__gt__", 
    "__iadd__", "__iand__", "__iconcat__", "__idiv__", "__ifloordiv__", 
    "__ilshift__", "__imod__", "__imul__", "__index__", "__inv__", "__invert__",
    "__ior__", "__ipow__", "__irepeat__", "__irshift__", "__isub__", 
    "__itruediv__", "__ixor__", "__le__", "__lt__", "__ne__", "__neg__",
    "__not__", "__package__", "__pos__", "__repeat__", "__setitem__",
    "__setslice__", "__radd__", "__rsub__", "__rmul__", "__rfloordiv__",
    "__rdiv__", "__rtruediv__", "__rmod__", "__rdivmod__", "__rpow__", 
    "__rlshift__",  "__rand__",  "__ror__",  "__rxor__",  # "__rrshift__",
]


# operator_hooks = [name for name in dir(operator) if name.startswith('__') and 
#                   name.endswith('__')]
def create_reversible_func(func_name, rfunc_name):
  def reversible_func(self, arg):
    def TryReverseIfNoRegular(df):
      if func_name in dir(df) and type(arg) == Later:
        return getattr(df, func_name)(arg.applyFcns(self.origDf))
      elif func_name in dir(df) and type(arg) != Later:
        return getattr(df, func_name)(arg)
      elif func_name not in dir(df) and type(arg) == Later:
        return getattr(arg.applyFcns(self.origDf), rfunc_name)(df)
      else:
        return getattr(arg, rfunc_name)(df)
    self.todo.append(TryReverseIfNoRegular)
    return self
  return reversible_func


def instrument_operator_hooks(cls):
  def add_hook(name):
    def op_hook(self, *args, **kw):
      if len(args) > 0 and type(args[0]) == Later:
        self.todo.append(lambda df: getattr(df, name)(args[0].applyFcns(self.origDf)))
      else:  
        self.todo.append(lambda df: getattr(df, name)(*args, **kw))
      return self

    try:
      setattr(cls, name, op_hook)
    except (AttributeError, TypeError):
      pass  # skip __name__ and __doc__ and the like

  for hook_name in normal_operators:
    add_hook(hook_name)

  for func_name, rfunc_name in reversible_operators:
    setattr(cls, func_name, create_reversible_func(func_name, rfunc_name))

  return cls


@instrument_operator_hooks
class Later(object):
  """Object which represents a computation to be carried out later.

  The Later object allows us to save computation that cannot currently be 
  executed. It will later receive a DataFrame as an input, and all computation 
  will be carried out upon this DataFrame object.

  Thus, we can refer to columns of the DataFrame as inputs to functions without 
  having the DataFrame currently available:
  In : diamonds >> dfilter(X.carat > 4) >> select(X.carat, X.price)
  Out:
         carat  price
  25998   4.01  15223
  25999   4.01  15223
  27130   4.13  17329
  27415   5.01  18018
  27630   4.50  18531

  The special Later name, "_" will refer to the entire DataFrame. For example, 
  In: diamonds >> sample_n(6) >> select(X.carat, X.price) >> X._.T
  Out:
           18966    19729   9445   49951    3087    33128
  carat     1.16     1.52     0.9    0.3     0.74    0.31
  price  7803.00  8299.00  4593.0  540.0  3315.00  816.00
  """
  def __init__(self, name):
    self.name = name
    if name == "_":
      self.todo = [lambda df: df]
    else:
      self.todo = [lambda df: df[self.name]]
  
  def applyFcns(self, df):
    self.origDf = df
    stmt = df
    for func in self.todo:
      stmt = func(stmt)
    return stmt
    
  def __getattr__(self, attr):
    self.todo.append(lambda df: getattr(df, attr))
    return self

  def __call__(self, *args, **kwargs):
    self.todo.append(lambda foo: foo.__call__(*args, **kwargs))
    return self

  def __rrshift__(self, df):
    otherDf = DplyFrame(df.copy(deep=True))
    return self.applyFcns(otherDf)



def CreateLaterFunction(fcn, *args, **kwargs):
  laterFcn = Later("_FUNCTION")
  # laterFcn = Later(fcn.func_name + "_FUNCTION")
  laterFcn.fcn = fcn
  laterFcn.args = args
  laterFcn.kwargs = kwargs
  def apply_function(self, df):
    self.origDf = df
    args = [a.applyFcns(self.origDf) if type(a) == Later else a 
        for a in self.args]
    kwargs = {k: v.applyFcns(self.origDf) if type(v) == Later else v 
        for k, v in six.iteritems(self.kwargs)}
    return self.fcn(*args, **kwargs)
  laterFcn.todo = [lambda df: apply_function(laterFcn, df)]
  return laterFcn
  

def DelayFunction(fcn):
  def DelayedFcnCall(*args, **kwargs):
    # Check to see if any args or kw are Later. If not, return normal fcn.
    checkIfLater = lambda x: type(x) == Later
    if (len(list(filter(checkIfLater, args))) == 0 and 
        len(list(filter(checkIfLater, list(kwargs.values())))) == 0):
      return fcn(*args, **kwargs)
    else:
      return CreateLaterFunction(fcn, *args, **kwargs)

  return DelayedFcnCall


class DplyFrame(DataFrame):
  """A subclass of the pandas DataFrame with methods for function piping.

  This class implements two main features on top of the pandas DataFrame. First,
  dplyr-style groups. In contrast to SQL-style or pandas style groups, rows are 
  not collapsed and replaced with a function value.
  Second, >> is overloaded on the DataFrame so that functions on the right-hand
  side of this equation are called on the object. For example,
  $ df >> select(X.carat)
  will call a function (created from the "select" call) on df.

  Currently, these inputs need to be one of the following:
  * A "Later" 
  * The "ungroup" function call
  * A function that returns a pandas DataFrame or DplyFrame.
  """
  _metadata = ["_grouped_on", "_group_dict"]

  def __init__(self, *args, **kwargs):
    super(DplyFrame, self).__init__(*args, **kwargs)
    self._grouped_on = None
    self._group_dict = None
    self._current_group = None
    if len(args) == 1 and isinstance(args[0], DplyFrame):
      self._copy_attrs(args[0])

  def _copy_attrs(self, df):
    for attr in self._metadata:
      self.__dict__[attr] = getattr(df, attr, None)

  @property
  def _constructor(self):
    return DplyFrame

  def CreateGroupIndices(self, names, values):
    final_filter = pandas.Series([True for t in range(len(self))])
    final_filter.index = self.index
    for (name, val) in zip(names, values):
      final_filter = final_filter & (self[name] == val)
    return final_filter

  def group_self(self, names):
    self._grouped_on = names
    values = [set(self[name]) for name in names]  # use dplyr here?
    self._group_dict = {v: self.CreateGroupIndices(names, v) for v in 
        itertools.product(*values)}

  def apply_on_groups(self, delayedFcn, otherDf):
    self.group_self(self._grouped_on)  # TODO: think about removing
    groups = []
    for group_vals, group_inds in six.iteritems(self._group_dict):
      subsetDf = otherDf[group_inds]
      if len(subsetDf) > 0:
        subsetDf._current_group = dict(list(zip(self._grouped_on, group_vals)))
        groups.append(delayedFcn(subsetDf))

    outDf = DplyFrame(pandas.concat(groups))
    outDf.index = list(range(len(outDf)))
    return outDf

  def __rshift__(self, delayedFcn):
    otherDf = DplyFrame(self.copy(deep=True))

    if type(delayedFcn) == Later:
      return delayedFcn.applyFcns(self)

    if delayedFcn == UngroupDF:
      return delayedFcn(otherDf)

    if self._group_dict:
      outDf = self.apply_on_groups(delayedFcn, otherDf)
      return outDf
    else:
      return DplyFrame(delayedFcn(otherDf))


def dfilter(*args):
  """Filters rows of the data that meet input criteria.

  Giving multiple arguments to dfilter is equivalent to a logical "and".
  In: df >> dfilter(X.carat > 4, X.cut == "Premium")
  # Out:
  # carat      cut color clarity  depth  table  price      x  ...
  #  4.01  Premium     I      I1   61.0     61  15223  10.14
  #  4.01  Premium     J      I1   62.5     62  15223  10.02
  
  As in pandas, use bitwise logical operators like |, &:
  In: df >> dfilter((X.carat > 4) | (X.cut == "Ideal")) >> head(2)
  # Out:  carat    cut color clarity  depth ...
  #        0.23  Ideal     E     SI2   61.5     
  #        0.23  Ideal     J     VS1   62.8     
  """
  def f(df):
    # TODO: This function is a candidate for improvement!
    final_filter = pandas.Series([True for t in range(len(df))])
    final_filter.index = df.index
    for arg in args:
      stmt = arg.applyFcns(df)
      final_filter = final_filter & stmt
    if final_filter.dtype != bool:
      raise Exception("Inputs to filter must be boolean")
    return df[final_filter]
  return f


# @DelayFunction
def select(*args):
  """Select specific columns from DataFrame. 

  Output will be DplyFrame type. Order of columns will be the same as input into
  select.
  In : diamonds >> select(X.color, X.carat) >> head(3)
  Out:
    color  carat
  0     E   0.23
  1     E   0.21
  2     E   0.23
  """
  names = [column.name for column in args]
  return X._[[column.name for column in args]]
  # def get_names(df): return df[names]
  # return get_names(names)
  # return DelayFunction(lambda df: df[names])


def mutate(**kwargs):
  """Adds a column to the DataFrame.

  This can use existing columns of the DataFrame as input.

  In : (diamonds >> 
          mutate(carat_bin=X.carat.round()) >> 
          group_by(X.cut, X.carat_bin) >> 
          summarize(avg_price=X.price.mean()))
  Out:
         avg_price  carat_bin        cut
  0     863.908535          0      Ideal
  1    4213.864948          1      Ideal
  2   12838.984078          2      Ideal
  ...
  27  13466.823529          3       Fair
  28  15842.666667          4       Fair
  29  18018.000000          5       Fair
  """
  def addColumns(df):
    for key, val in six.iteritems(kwargs):
      if type(val) == Later:
        df[key] = val.applyFcns(df)
      else:
        df[key] = val
    return df
  return addColumns


def group_by(*args):
  def GroupDF(df):
    df.group_self([arg.name for arg in args])
    return df
  return GroupDF


def summarize(**kwargs):
  def CreateSummarizedDf(df):
    input_dict = {k: val.applyFcns(df) for k, val in six.iteritems(kwargs)}
    if len(input_dict) == 0:
      return DplyFrame({}, index=index)
    if hasattr(df, "_current_group") and df._current_group:
      input_dict.update(df._current_group)
    index = [0]
    return DplyFrame(input_dict, index=index)
  return CreateSummarizedDf


def UngroupDF(df):
  df._grouped_on = None
  df._group_dict = None
  return df


def ungroup():
  return UngroupDF
  

def arrange(*args):
  """Sort DataFrame by the input column arguments.

  In : diamonds >> sample_n(5) >> arrange(X.price) >> select(X.depth, X.price)
  Out:
         depth  price
  28547   61.0    675
  35132   59.1    889
  42526   61.3   1323
  3468    61.6   3392
  23829   62.0  11903
  """
  # TODO: add in descending and ascending
  names = [column.name for column in args]
  return lambda df: DplyFrame(df.sort(names))


def head(*args, **kwargs):
  """Returns first n rows"""
  return X._.head(*args, **kwargs)


def sample_n(n):
  """Randomly sample n rows from the DataFrame"""
  # return X._.sample(n=n)
  return lambda df: DplyFrame(df.sample(n))


def sample_frac(frac):
  """Randomly sample `frac` fraction of the DataFrame"""
  # return X._.sample(frac=frac)
  return lambda df: DplyFrame(df.sample(frac=frac))


def sample(*args, **kwargs):
  """Convenience method that calls into pandas DataFrame's sample method"""
  return X._.sample(*args, **kwargs)


nrow = X._.__len__


@DelayFunction
def PairwiseGreater(series1, series2):
  index = series1.index
  newSeries = pandas.Series([max(s1, s2) for s1, s2 in zip(series1, series2)])
  newSeries.index = index
  return newSeries
