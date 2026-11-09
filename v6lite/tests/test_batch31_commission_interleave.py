"""Native commissions must not revoke economically unchanged TP handoff proof."""
from ib_insync import CommissionReport
from v6lite.tests.test_batch28b_overnight_refresh import overnight_run,_no_network,_safety
from v6lite.tests.test_batch28c_ack_ordering import StatusFirstServer
from v6lite.tests.test_batch28d_native_final_tp import NativeFinalTPServer


def fee(server):
    exec_id=server.rig._b26_entry_fill.exec_id
    server.wrapper.commissionReport(CommissionReport(execId=exec_id,commission=1.01,currency='USD'))


class FeeBetweenReductionComponents(StatusFirstServer):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.ib.commissionReportEvent+=self.adapter._on_commission
        self.fee_checks=[]
    def release_ack(self):
        if self.ack_released:return
        super().release_ack()
        self.later(.04,self.interleave)
    def interleave(self):
        before=self.rig.engine._protection_reduction_proofs.get(self.pid)
        fingerprint=self.rig.engine._protection_reduction_fingerprint(self.rig.store.protection(self.pid))
        fee(self)
        after=self.rig.engine._protection_reduction_proofs.get(self.pid)
        self.fee_checks.append((before is not None,after is before,
            fingerprint==self.rig.engine._protection_reduction_fingerprint(self.rig.store.protection(self.pid))))


def test_B31_native_fee_between_reduction_ACK_components_keeps_fast_TP(overnight_run,monkeypatch,request):
    rig,pid,harness=overnight_run()
    server=FeeBetweenReductionComponents(rig,pid,harness,monkeypatch,request)
    server.drive()
    assert server.fee_checks==[(True,True,True)]
    assert server.tp_wire-server.ack_at<=1.
    assert server.fill_at-server.ack_at<=5.
    _safety(rig)


class FeeBeforeFinalCancel(NativeFinalTPServer):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.ib.commissionReportEvent+=self.adapter._on_commission
        self.fee_checks=[]
    def cancel_response(self):
        before=self.rig.engine._protection_final_cancel_proofs.get(self.pid)
        fingerprint=self.rig.engine._protection_reduction_fingerprint(self.rig.store.protection(self.pid))
        fee(self)
        after=self.rig.engine._protection_final_cancel_proofs.get(self.pid)
        self.fee_checks.append((before is not None,after is before,
            fingerprint==self.rig.engine._protection_reduction_fingerprint(self.rig.store.protection(self.pid))))
        return super().cancel_response()


def test_B31_native_fee_during_final_cancel_keeps_fast_TP3(overnight_run,monkeypatch,request):
    rig,pid,harness=overnight_run()
    server=FeeBeforeFinalCancel(rig,pid,harness,monkeypatch,request)
    server.last_share();server.final()
    assert server.fee_checks==[(True,True,True)]
    assert server.final_tp_wire-server.cancel_at<=1.
    assert server.final_fill_at-server.cancel_at<=5.
    assert rig.store.position(pid)['qty_open']==0
    _safety(rig)
