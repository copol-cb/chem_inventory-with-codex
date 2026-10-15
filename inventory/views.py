from django.core.paginator import Paginator
from django.shortcuts import render, redirect
from django.utils import timezone
from .models import ChemicalRegulation, MyInventory
import json # [NEW] 데이터를 JSON으로 변환하기 위해 필요

# ====================================================
# [설정] 사업장별 접속 코드 (비밀번호)
# ====================================================
SITE_ACCESS_CODES = {
    "ourhome001": "안산공장",
    "ourhome1": "용인1공장",
    "ourhome2": "용인2공장",
    "ourhome48": "제천공장",
    "rngf": "음성공장",
    "ourhome11": "계룡공장",
    "ourhome300": "구미공장",
    "aw9969": "양산공장",
    "ourhome1001": "동서울물류센터",
    "ys21002": "양산2물류센터",
    "yi31003": "용인3물류센터",
    "hn1004": "호남물류센터",
    "jj1005": "제주물류센터",
    "e002463": "MASTER", 
}
SITE_MAP = {
    # 제조본부
    "안산공장": "제조본부", "용인1공장": "제조본부", "용인2공장": "제조본부",
    "제천공장": "제조본부", "음성공장": "제조본부", "계룡공장": "제조본부",
    "구미공장": "제조본부", "양산공장": "제조본부",
    # 물류본부
    "동서울물류센터": "물류본부", "용인3물류센터": "물류본부", 
    "호남물류센터": "물류본부", "양산2물류센터": "물류본부", "제주물류센터": "물류본부"
}

def inventory_access(request):
    if request.method == 'POST':
        input_code = request.POST.get('access_code', '').strip()
        if input_code in SITE_ACCESS_CODES:
            matched_site = SITE_ACCESS_CODES[input_code]
            request.session['allowed_site'] = matched_site
            return redirect('inventory_list') 
        else:
            return render(request, 'inventory/access.html', {'error': '코드가 올바르지 않습니다.'})
    return render(request, 'inventory/access.html')

def inventory_logout(request):
    request.session.flush() 
    return redirect('inventory_access') 


# ====================================================
# [헬퍼 함수] 사용시설 보유량(톤) 계산 로직 (핵심!)
# ====================================================
def calculate_usage_ton_process(item_or_val, percent_val=0.0):
    """
    사용시설의 최대보유량(톤)을 계산하는 로직
    - item_or_val: MyInventory 객체(조회 시) 또는 딕셔너리(등록 시)
    - percent_val: 등록 시에는 함량을 별도로 받음
    """
    # 1. 데이터 추출 (객체인지 딕셔너리인지 구분)
    if isinstance(item_or_val, MyInventory):
        u_max = item_or_val.usage_max        # 1회 최대 사용량(kg)
        u_water = item_or_val.water_input    # 물 투입량(kg)
        u_vol = item_or_val.usage_facility_vol # 수조 용량(m3)
        try:
            pct = float(item_or_val.percentage)
        except (TypeError, ValueError):
            pct = 0.0  # 등록 시와 동일하게 영업비밀 등 비수치 함량 처리
        cas = item_or_val.cas_no             # CAS No.
    else:
        u_max = item_or_val.get('usage_max', 0.0)
        u_water = item_or_val.get('water_input', 0.0)
        u_vol = item_or_val.get('usage_facility_vol', 0.0)
        cas = item_or_val.get('cas_no', '')
        pct = percent_val

    # -----------------------------------------------------------
    # [Case 2] 희석 사용 (물 투입량 > 0 그리고 수조 용량 > 0)
    # -----------------------------------------------------------
    if u_water > 0 and u_vol > 0:
        # 1. 희석 후 농도 계산
        # 공식: (사용량 * 원액함량) / (사용량 + 물량)
        total_diluted_weight = u_max + u_water
        if total_diluted_weight == 0:
            return 0.0
            
        final_percent = (float(u_max) * float(pct)) / total_diluted_weight
        
        # 2. 규제 여부 확인 (DB 조회)
        reg_candidates = ChemicalRegulation.objects.filter(cas_no=cas)
        is_regulated_concentration = False
        
        for reg in reg_candidates:
            # 희석된 농도가 규제 기준 이상인지 확인
            if final_percent >= reg.limit_concentration:
                is_regulated_concentration = True
                break
        
        # 3. 판정 결과에 따른 보유량 산정
        if is_regulated_concentration:
            # 위험함 -> 수조 용량(m3)을 그대로 톤으로 적용 (A안)
            return u_vol 
        else:
            # 안전함 -> 보유량 0으로 간주
            return 0.0

    # -----------------------------------------------------------
    # [Case 1] 희석 아님 (일반 사용)
    # -----------------------------------------------------------
    else:
        # 1회 최대 사용량(kg)을 톤으로 변환
        return u_max / 1000.0


def calculate_total_ton(item):
    """
    [관리대장용] 전체 최대보유량(톤) 합계 계산
    """
    total_ton = 0.0
    
    # 1. 저장시설 (용량m3 * 비중 * 1000 -> kg -> ton) = (용량 * 비중)
    if item.is_storage:
        total_ton += (item.storage_vol * item.specific_gravity)
        
    # 2. 보관시설 (kg -> ton)
    if item.is_keeping:
        total_ton += (item.keeping_max / 1000.0)
        
    # 3. 사용시설 (케이스별 로직 적용)
    if item.is_usage:
        total_ton += calculate_usage_ton_process(item)

    return total_ton


# ====================================================
# [수정됨] 대시보드 통계 계산 함수 (제품 수 로직 개선)
# ====================================================
def calculate_dashboard_stats(items):
    # 1. 변수 초기화
    product_count = 0       # 제품 수 (묶음 단위)
    prev_item = None        # 이전 아이템 비교용

    # 2. 통계 변수들
    usage_map = {
        "1": "1. 원료 및 첨가제", "2": "2. 공정보조물질", "3": "3. 시설/설비 유지보수",
        "4": "4. 시험용 시약", "5": "5. 폐수처리약품", "6": "6. 보관/저장(운송업/창고업 해당)",
        "7": "7. 기타"
    }
    usage_counts = {label: 0 for label in usage_map.values()}
    usage_counts["8. 기타(미지정)"] = 0

    danger_types = ["인체급성", "인체만성", "생태유해", "사고대비", "제한물질", "금지물질"]
    danger_stats = {
        dtype: {"lowest": 0, "report": 0, "permission": 0, "total": 0} 
        for dtype in danger_types
    }
    grand_totals = {"lowest": 0, "report": 0, "permission": 0, "total": 0}

    # 3. 전체 아이템 순회 (한 번의 루프로 제품 수 + 통계 모두 계산)
    for item in items:
        # ---------------------------------------------------
        # [A] 제품 수 카운팅 (목록 뷰와 동일한 그룹화 로직)
        # ---------------------------------------------------
        is_new_group = False
        if prev_item is None:
            is_new_group = True
        else:
            # 이름, 사업장, 등록자가 같고 시간 차이가 2초 미만이면 같은 그룹
            time_diff = abs((item.created_at - prev_item.created_at).total_seconds())
            is_same = (
                item.product_name == prev_item.product_name and
                item.registrant == prev_item.registrant and
                item.site_name == prev_item.site_name and
                time_diff < 2.0
            )
            if not is_same:
                is_new_group = True
        
        if is_new_group:
            product_count += 1
            prev_item = item  # 그룹 기준점 갱신

        # ---------------------------------------------------
        # [B] 용도 및 유해화학물질 통계 (화학물질 단위 집계)
        # ---------------------------------------------------
        
        # (1) 용도 카운팅
        u_pur = str(item.usage_purpose).strip()
        matched_code = u_pur[:1]
        if matched_code in usage_map:
            usage_counts[usage_map[matched_code]] += 1
        else:
            usage_counts["8. 기타(미지정)"] += 1

        # (2) 유해화학물질 카운팅
        result_text = str(item.is_danger_result)
        if "안전" in result_text: continue

        for dtype in danger_types:
            if dtype in result_text:
                danger_stats[dtype]["total"] += 1
                grand_totals["total"] += 1 

                if dtype in ["제한물질", "금지물질"]:
                    continue

                if "영업허가" in result_text and dtype in result_text:
                    danger_stats[dtype]["permission"] += 1
                    grand_totals["permission"] += 1 
                elif "영업신고" in result_text and dtype in result_text:
                    danger_stats[dtype]["report"] += 1
                    grand_totals["report"] += 1 
                else:
                    danger_stats[dtype]["lowest"] += 1
                    grand_totals["lowest"] += 1 

    return product_count, usage_counts, danger_stats, grand_totals

# ====================================================
# [관리 대장 조회] (페이지네이션 적용됨)
# ====================================================
def inventory_list(request):
    user_site = request.session.get('allowed_site')
    if not user_site:
        return redirect('inventory_access')

    current_mode = request.GET.get('mode', 'active') 

    # 1. 데이터 가져오기
    items = MyInventory.objects.all().order_by('-created_at', 'product_name', 'id')

    if current_mode == 'trash':
        items = items.filter(is_deleted=True)
    else:
        items = items.filter(is_deleted=False)

    # 2. 필터링 (MASTER)
    div_filter = None
    site_filter = None
    if user_site == 'MASTER':
        div_filter = request.GET.get('division')
        site_filter = request.GET.get('site_name')
        if div_filter: items = items.filter(division=div_filter)
        if site_filter: items = items.filter(site_name=site_filter)
        
        context_vars = {
            'is_master': True, 'selected_division': div_filter, 'selected_site': site_filter,
            'current_user_site': user_site, 'current_mode': current_mode
        }
    else:
        items = items.filter(site_name=user_site)
        context_vars = {
            'is_master': False, 'current_user_site': user_site, 'current_mode': current_mode
        }

    # 3. 대시보드 통계 (수정됨: 반환값 4개 받기)
    d_total, d_usage, d_danger, d_grand = calculate_dashboard_stats(items)
    
    context_vars['dashboard_data'] = {
        'total_count': d_total, 
        'usage_stats': json.dumps(d_usage), 
        'danger_stats': d_danger,
        'grand_totals': d_grand, # [NEW] 템플릿으로 합계 정보 전달
    }

    # 4. 데이터 그룹화
    grouped_items = []
    if items.exists():
        first_ton = calculate_total_ton(items[0])
        current_group = {
            'common': items[0], 'components': [items[0]], 'row_span': 1, 'total_ton': first_ton
        }
        for item in items[1:]:
            is_same_info = (
                item.product_name == current_group['common'].product_name and
                item.registrant == current_group['common'].registrant and
                item.site_name == current_group['common'].site_name and
                item.division == current_group['common'].division
            )
            time_diff = abs((item.created_at - current_group['common'].created_at).total_seconds())
            if is_same_info and time_diff < 2.0:
                current_group['components'].append(item)
                current_group['row_span'] += 1
            else:
                grouped_items.append(current_group)
                new_ton = calculate_total_ton(item)
                current_group = {
                    'common': item, 'components': [item], 'row_span': 1, 'total_ton': new_ton 
                }
        grouped_items.append(current_group)

    # ==========================================================
    # [NEW] 페이지네이션 (20개씩 자르기)
    # ==========================================================
    page = request.GET.get('page', '1')  # 페이지 번호 가져오기
    paginator = Paginator(grouped_items, 20) # 20개씩 자르기
    page_obj = paginator.get_page(page)      # 해당 페이지 데이터 가져오기

    # 안전 판정 성분도 CAS에 등록된 전체 규제 기준을 확인할 수 있게 한다.
    page_items = [item for group in page_obj for item in group['components']]
    regulations_by_cas = {}
    for regulation in ChemicalRegulation.objects.filter(
        cas_no__in={item.cas_no for item in page_items}
    ).exclude(regulation_type__contains='일반화학물질').order_by('regulation_type', 'pk'):
        regulations_by_cas.setdefault(regulation.cas_no, []).append(regulation)
    for item in page_items:
        item.quantity_regulations = regulations_by_cas.get(item.cas_no, [])
        item.component_total_ton = calculate_total_ton(item)

    context_vars['grouped_items'] = page_obj # 템플릿에는 잘린 데이터만 전달
    # ==========================================================

    return render(request, 'inventory/inventory_list.html', context_vars)


# ====================================================
# [신규 등록] (수정 완료: 파일 없어도 저장 가능, 중복 코드 제거)
# ====================================================
def inventory_add(request):
    # [1] 세션에서 로그인 정보 가져오기
    user_site = request.session.get('allowed_site')
    if not user_site:
        return redirect('inventory_access')

    # [2] 관리자 여부 판별
    is_master = (user_site == 'MASTER')
    
    # [3] 일반 사용자용 본부 자동 찾기
    user_division = ""
    if not is_master:
        user_division = SITE_MAP.get(user_site, "")

    if request.method == 'POST':
        # [4] 사업장 정보 결정
        if is_master:
            final_division = request.POST.get('division')
            final_site = request.POST.get('site_name')
        else:
            final_division = user_division
            final_site = user_site

        # 나머지 입력값 가져오기
        input_registrant = request.POST.get('registrant')
        current_time = timezone.now()

        index = 0
        while True:
            # 제품명 키 확인 (없으면 루프 종료)
            p_name_key = f'product_name_{index}'
            if p_name_key not in request.POST: break
            
            product_name = request.POST.get(p_name_key)
            
            # [MSDS 파일 처리]
            msds_file_obj = request.FILES.get(f'msds_file_{index}')
            
            # 1. 파일 필수 체크 해제 (보안 정책 반영: 파일 없어도 통과)
            if not is_master and not msds_file_obj:
                pass # 파일이 없어도 에러 없이 진행

            # 2. 파일이 있다면 확장자 검사 (.pdf만 허용)
            if msds_file_obj:
                filename = str(msds_file_obj.name).lower()
                if not filename.endswith('.pdf'):
                    msds_file_obj = None # PDF가 아니면 저장 안 함

            state = request.POST.get(f'state_{index}')
            purpose = request.POST.get(f'usage_purpose_{index}')
            detail_input = request.POST.get(f'usage_detail_{index}')
            
            # 시설 정보 가져오기
            is_storage_chk = request.POST.get(f'is_storage_{index}') == 'on'
            s_loc = request.POST.get(f'storage_loc_{index}')
            try: s_vol = float(request.POST.get(f'storage_vol_{index}'))
            except: s_vol = 0.0
            try: s_sg = float(request.POST.get(f'specific_gravity_{index}'))
            except: s_sg = 0.0

            is_keeping_chk = request.POST.get(f'is_keeping_{index}') == 'on'
            k_loc = request.POST.get(f'keeping_loc_{index}')
            try: k_max = float(request.POST.get(f'keeping_max_{index}'))
            except: k_max = 0.0

            is_usage_chk = request.POST.get(f'is_usage_{index}') == 'on'
            u_loc = request.POST.get(f'usage_loc_{index}')
            try: u_max = float(request.POST.get(f'usage_max_{index}'))
            except: u_max = 0.0
            try: u_water = float(request.POST.get(f'water_input_{index}'))
            except: u_water = 0.0
            try: u_vol = float(request.POST.get(f'usage_facility_vol_{index}'))
            except: u_vol = 0.0

            cas_list = request.POST.getlist(f'cas_no_{index}[]')
            percent_list = request.POST.getlist(f'percentage_{index}[]')

            for i in range(len(cas_list)):
                current_cas = cas_list[i].strip()
                current_percent_str = percent_list[i].strip() # [수정] 문자열 그대로 가져옴
                if not current_cas: continue 

                # [수정됨] 함량 처리 로직 (핵심!)
                # 1. 계산용 숫자 변수 (percent_calc_val)
                # 2. 저장용 문자 변수 (percent_save_val)
                
                percent_calc_val = 0.0 # 기본값 0 (텍스트일 경우 계산에서 제외됨)
                percent_save_val = current_percent_str

                # 숫자로 변환 시도
                try:
                    percent_calc_val = float(current_percent_str)
                except ValueError:
                    # 변환 실패 = 텍스트임 (예: "영업비밀")
                    # 계산용 값은 0.0으로 유지, 저장용 값은 텍스트 그대로 유지
                    percent_calc_val = 0.0

                # [톤수 계산] -> 계산용 변수(percent_calc_val) 사용
                current_total_ton = 0.0
                if is_storage_chk: current_total_ton += (s_vol * s_sg)
                if is_keeping_chk: current_total_ton += (k_max / 1000.0)
                if is_usage_chk:
                    temp_data = {'usage_max': u_max, 'water_input': u_water, 'usage_facility_vol': u_vol, 'cas_no': current_cas}
                    # 여기서도 숫자형 값을 넘겨줍니다
                    usage_ton = calculate_usage_ton_process(temp_data, percent_calc_val)
                    current_total_ton += usage_ton
                
                daily_usage_ton = current_total_ton

                # [규제 판정 로직] -> 계산용 변수(percent_calc_val) 사용
                reg_candidates = ChemicalRegulation.objects.filter(cas_no=current_cas)
                msgs_concentration = set()
                msgs_quantity = set()
                is_general_chem = False
                has_record = reg_candidates.exists()
                
                for reg in reg_candidates:
                    reg_type = str(reg.regulation_type).strip()
                    limit_conc = reg.limit_concentration
                    
                    if '일반화학물질' in reg_type:
                        is_general_chem = True
                        continue

                    compare_percent = percent_calc_val
                    if is_usage_chk and u_water > 0 and u_vol > 0:
                         if (u_max + u_water) > 0:
                             compare_percent = (u_max * percent_calc_val) / (u_max + u_water)
                    
                    if compare_percent >= limit_conc:
                        if '금지' in reg_type: 
                             raw = reg.regulation_details if reg.regulation_details else "내용 없음"
                             msgs_concentration.add(f"🚫 금지물질({limit_conc}% 이상)__DETAIL__{raw}")
                        if '제한' in reg_type:
                            raw = reg.regulation_details if reg.regulation_details else "내용 없음"
                            msgs_concentration.add(f"🚫 제한물질({limit_conc}% 이상)__DETAIL__{raw}")
                        if '사고' in reg_type: msgs_concentration.add(f"⚠️ 사고대비({limit_conc}% 이상)")
                        if '급성' in reg_type: msgs_concentration.add(f"☣️ 인체급성({limit_conc}% 이상)")
                        if '만성' in reg_type: msgs_concentration.add(f"☣️ 인체만성({limit_conc}% 이상)")
                        if '생태' in reg_type: msgs_concentration.add(f"☣️ 생태유해({limit_conc}% 이상)")
                        
                        if '금지' in reg_type: msgs_quantity.add("🚫 취급 금지 대상 (수량 무관)")
                        if '제한' in reg_type: msgs_quantity.add("🚫 용도 제한 대상 (수량 무관)")
                        if not any(k in reg_type for k in ['금지','제한','사고','급성','만성','생태']):
                             msgs_concentration.add(f"⚠️ {reg_type}({limit_conc}% 이상)")

                        if any(k in reg_type for k in ['사고', '급성', '만성', '생태']):
                            qty_lowest = reg.limit_qty_lowest if reg.limit_qty_lowest else 0.0
                            qty_lower = reg.limit_qty_lower if reg.limit_qty_lower else 0.0
                            
                            if qty_lowest == 0 and qty_lower == 0: pass 
                            else:
                                short_type_name = reg_type
                                if '사고' in reg_type: short_type_name = "사고대비"
                                elif '급성' in reg_type: short_type_name = "인체급성"
                                elif '만성' in reg_type: short_type_name = "인체만성"
                                elif '생태' in reg_type: short_type_name = "생태유해"

                                if daily_usage_ton >= qty_lower:
                                    msgs_quantity.add(f"⚠️ {short_type_name}(영업허가 대상)")
                                elif daily_usage_ton >= qty_lowest:
                                    msgs_quantity.add(f"⚠️ {short_type_name}(영업신고 대상)")
                                else:
                                    msgs_quantity.add(f"✅ {short_type_name}(규정수량 미만)")

                final_parts = []
                if msgs_concentration:
                    reasons = " /// ".join(sorted(list(msgs_concentration)))
                    final_parts.append(f"규제 기준농도 [{reasons}]")
                if msgs_quantity:
                    reasons = " /// ".join(sorted(list(msgs_quantity)))
                    final_parts.append(f"규제 판정: [{reasons}]")

                if final_parts: is_danger_result = "__BcSplit__".join(final_parts)
                elif is_general_chem: is_danger_result = "✅ 안전 (해당없음)"
                elif has_record: is_danger_result = "✅ 안전 (규제미만)"
                else: is_danger_result = "✅ 안전 (정보없음)"

                # [화학물질 공식 명칭 찾기]
                official_name = ""
                found_chems = ChemicalRegulation.objects.filter(cas_no=current_cas)
                candidate_names = [c.name_korean.split('[')[0].strip() for c in found_chems if c.name_korean]
                if candidate_names: 
                    official_name = min(candidate_names, key=len)

                # [DB 저장] (여기가 최종 저장 코드입니다. 중복 없음!)
                MyInventory.objects.create(
                    division=final_division, 
                    site_name=final_site,
                    registrant=input_registrant,
                    product_name=product_name, 
                    msds_file=msds_file_obj, 
                    cas_no=current_cas, 
                    chemical_name=official_name,
                    percentage=percent_save_val, 
                    state=state, 
                    usage_purpose=purpose, 
                    usage_detail=detail_input,
                    is_storage=is_storage_chk, storage_loc=s_loc, storage_vol=s_vol, specific_gravity=s_sg,
                    is_keeping=is_keeping_chk, keeping_loc=k_loc, keeping_max=k_max, 
                    is_usage=is_usage_chk, usage_loc=u_loc, usage_max=u_max, water_input=u_water, usage_facility_vol=u_vol,
                    is_danger_result=is_danger_result, created_at=current_time  
                )
            index += 1
        return redirect('inventory_list')

    # [5] GET 요청 시 (템플릿에 정보 전달)
    return render(request, 'inventory/inventory_add.html', {
        'current_user_site': user_site,
        'is_master': is_master,
        'user_division': user_division 
    })
def inventory_delete(request, pk):
    if request.session.get('allowed_site') != 'MASTER':
        return redirect('inventory_list')
    try:
        target = MyInventory.objects.get(pk=pk)
        MyInventory.objects.filter(
            division=target.division, site_name=target.site_name,
            product_name=target.product_name, registrant=target.registrant,
            created_at=target.created_at
        ).delete()
    except MyInventory.DoesNotExist:
        pass
    return redirect('inventory_list')

# ====================================================
# [일괄 삭제 (Soft Delete)] -> 휴지통 보내기
# ====================================================
def inventory_delete_bulk(request):
    if request.session.get('allowed_site') != 'MASTER':
        return redirect('inventory_list')
    
    if request.method == 'POST':
        selected_ids = request.POST.getlist('selected_ids')
        for pk in selected_ids:
            try:
                target = MyInventory.objects.get(pk=pk)
                MyInventory.objects.filter(
                    division=target.division, site_name=target.site_name,
                    product_name=target.product_name, registrant=target.registrant,
                    created_at=target.created_at
                ).update(is_deleted=True) 
            except MyInventory.DoesNotExist:
                continue
    return redirect('inventory_list')

# ====================================================
# [일괄 복구 (Restore)] -> 휴지통에서 꺼내기
# ====================================================
def inventory_restore_bulk(request):
    if request.session.get('allowed_site') != 'MASTER':
        return redirect('inventory_list')
    
    if request.method == 'POST':
        selected_ids = request.POST.getlist('selected_ids')
        for pk in selected_ids:
            try:
                target = MyInventory.objects.get(pk=pk)
                MyInventory.objects.filter(
                    division=target.division, site_name=target.site_name,
                    product_name=target.product_name, registrant=target.registrant,
                    created_at=target.created_at
                ).update(is_deleted=False)
            except MyInventory.DoesNotExist:
                continue
    return redirect('/list/?mode=trash')
