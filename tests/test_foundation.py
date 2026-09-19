from datetime import datetime
import pytest
from jevtweet.contracts import Candidate, Factor, digest
from jevtweet.storage import Store, ConflictError, BudgetError

def test_timestamp_and_unknown_fields():
    with pytest.raises(ValueError): Candidate(text='x', content_available_at=datetime(2020,1,1))
    with pytest.raises(ValueError): Candidate(text='x', views=500)
    with pytest.raises(ValueError): Factor(question_id='x',type='noul',noul=.5,confidence=.9)

def test_storage_and_budget(tmp_path):
    s=Store(tmp_path)
    s.put('candidate','a',{'text':'one'})
    s.put('candidate','a',{'text':'one'})
    with pytest.raises(ConflictError):s.put('candidate','a',{'text':'two'})
    s.reserve('a',.6,1,30)
    with pytest.raises(BudgetError):s.reserve('b',.5,1,30)
    assert s.spending()['charged_or_reserved_usd']==.6
    assert digest({'b':1,'a':2})==digest({'a':2,'b':1})
