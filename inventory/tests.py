from django.test import TestCase

from .models import ChemicalRegulation, MyInventory
from .views import calculate_total_ton


class QuantityDetailsTests(TestCase):
    def setUp(self):
        session = self.client.session
        session['allowed_site'] = 'MASTER'
        session.save()

    def test_facility_sum_matches_example_without_percentage_multiplier(self):
        item = MyInventory(is_storage=True, storage_vol=93.85, specific_gravity=36.33,
                           is_keeping=True, keeping_max=71.97,
                           is_usage=True, usage_max=31.66, percentage='86.56')
        self.assertAlmostEqual(calculate_total_ton(item), 3409.67413)

    def test_diluted_usage_with_text_percentage(self):
        ChemicalRegulation.objects.create(cas_no='test', limit_concentration=5)
        item = MyInventory(cas_no='test', is_usage=True, usage_max=10,
                           water_input=90, usage_facility_vol=2, percentage='60')
        self.assertEqual(calculate_total_ton(item), 2)
        item.percentage = '영업비밀'
        self.assertEqual(calculate_total_ton(item), 0)

    def test_safe_and_permit_items_include_all_quantity_thresholds(self):
        ChemicalRegulation.objects.create(cas_no='test', name_korean='시험물질',
            regulation_type='인체급성', limit_concentration=5,
            limit_qty_lowest=0.4, limit_qty_lower=20, limit_qty_upper=250)
        for result in ['✅ 안전 (규제미만)', '규제 기준농도 [5% 이상]__BcSplit__규제 판정: [영업허가 대상]']:
            MyInventory.objects.create(cas_no='test', product_name=result,
                percentage='2', is_danger_result=result)
        response = self.client.get('/list/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '규정수량 기준 및 범위', count=2)
        self.assertContains(response, '0.4톤', count=2)
        self.assertContains(response, '20.0톤', count=2)
        self.assertContains(response, '250.0톤', count=2)
        self.assertContains(response, '상위 규정수량 이상', count=2)

    def test_missing_and_zero_thresholds_do_not_invent_ranges(self):
        for kind, lowest in [('제한물질', None), ('사고대비', 0)]:
            ChemicalRegulation.objects.create(cas_no='test', regulation_type=kind,
                limit_qty_lowest=lowest, limit_qty_lower=None, limit_qty_upper=None)
        MyInventory.objects.create(cas_no='test', product_name='test', percentage='1')
        response = self.client.get('/list/')
        self.assertContains(response, '미등록')
        self.assertContains(response, '0.0톤')
        self.assertContains(response, '수량과 무관하게')
        self.assertContains(response, '수량 범위를 표시하지 않습니다')
