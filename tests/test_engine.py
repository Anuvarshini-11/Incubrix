import pytest
from app.models import Timeline,Source,Segment,Word
from app.engine import History,cut,set_speed,duration,timeline_hash,caption_output_interval,add_caption,EditError
from app.rendering import caption_ass

def sample():
 return Timeline(timeline_id='t1',source=Source(media_id='m1',sha256='0'*64,duration_s=20),transcript=[Word(id=f'w{i}',start_s=i,end_s=i+.8,text=x) for i,x in enumerate(['welcome','to','the','channel','hello'])],segments=[Segment(id='s0',source_start_s=0,source_end_s=20)])
def test_cut_shifts_content_caption_mapping():
 t=sample();add_caption(t,'Welcome!','welcome to the channel');cap=t.captions[0];assert caption_output_interval(t,cap)==(0,3.8);cut(t,0,2);assert caption_output_interval(t,cap)==(0,1.8);assert duration(t)==18
def test_speed_keeps_anchor_and_changes_output_mapping():
 t=sample();add_caption(t,'Welcome!','welcome to the channel');set_speed(t,0,4,2);assert caption_output_interval(t,t.captions[0])==(0,1.9)
def test_undo_redo_exact():
 h=History(sample());h.apply(cut,0,2);assert duration(h.current)==18;h.undo();assert duration(h.current)==20;h.redo();assert duration(h.current)==18

def test_deterministic_hash():
 a=sample();b=sample();assert timeline_hash(a)==timeline_hash(b)
def test_whole_cut_rejected():
 with pytest.raises(EditError):cut(sample(),0,20)
def test_out_of_range_rejected():
 with pytest.raises(EditError):set_speed(sample(),1,30,2)
@pytest.mark.parametrize('start,end', [(-1,2),(2,1),(1,1),(0,21),(float('nan'),2)])
def test_invalid_cut_range_is_rejected_without_mutation(start,end):
 t=sample();before=timeline_hash(t)
 with pytest.raises(EditError):cut(t,start,end)
 assert timeline_hash(t)==before
def test_transcript_must_be_ordered():
 t=sample();t.transcript=[Word(id='later',start_s=3,end_s=4,text='later'),Word(id='earlier',start_s=1,end_s=2,text='earlier')]
 with pytest.raises(EditError,match='ordered'):History(t).apply(lambda timeline:None)
def test_caption_ass_uses_timeline_time_and_style():
 t=sample();add_caption(t,'Hello {there}','welcome to the channel');set_speed(t,0,4,2)
 text=caption_ass(t)
 assert 'Dialogue: 0,0:00:00.00,0:00:01.90' in text
 assert r'\fs36' in text and r'Hello \{there\}' in text
def test_speed_bounds():
 with pytest.raises(EditError):set_speed(sample(),1,2,10)
