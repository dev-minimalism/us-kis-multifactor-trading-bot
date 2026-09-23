# kis_trader.py - KIS 트레이더 (미국 주식 전용)
import json
from typing import Any, Dict, List, Optional

import requests

from multifactor_bot.brokers.kis_client import KISClient


class KISTrader:
  """KIS 거래 실행자 (미국 주식 전용)"""

  def __init__(self, config=None):
    """
    Args:
        config: 설정 딕셔너리 또는 모듈
    """
    self.client = KISClient('US', config=config)

    # Config 로드
    if config is None:
      try:
        import config_rsi_william as cfg
        config = cfg
      except ImportError:
        raise Exception("설정 파일을 찾을 수 없습니다.")

    # 계좌 정보
    if hasattr(config, 'KIS_ACCOUNT'):
      account = config.KIS_ACCOUNT
    elif isinstance(config, dict):
      account = config.get('KIS_ACCOUNT', '')
    else:
      raise Exception("KIS_ACCOUNT를 찾을 수 없습니다.")

    # 계좌 파싱
    parts = account.split('-')
    self.account_no = parts[0]
    self.product_code = parts[1] if len(parts) > 1 else "01"

    print(f"✅ KIS Trader 초기화 (US)")

  # ============================================================
  # 잔고 조회
  # ============================================================

  def get_balance(self, ref_ticker: str = "AAPL") -> Optional[Dict[str, Any]]:
    """미국 주식 주문가능금액 조회 (해외주식 매수가능금액조회 API)

    KIS 의 이 API 는 종목코드(ITEM_CD)와 주문단가(OVRS_ORD_UNPR)를 필수로 요구한다.
    금액 자체는 종목과 무관하므로 기준 종목 하나(AAPL)와 단가 1 로 조회한다.

    Returns:
        {
          'total_cash'    : USD 예수금 기준 주문가능외화금액 (ord_psbl_frcr_amt)
          'available_cash': 해외주문가능금액 (ovrs_ord_psbl_amt, 통합증거금이면 원화 환산분 포함)
          'exchange_rate' : 적용 환율 (exrt)
        }
        None → API 호출 실패. 호출자는 None 을 "잔고 0" 과 구분해서 다뤄야 한다.
                (DB 잔고를 0 으로 덮어쓰는 사고 방지)
    """
    tr_id = "VTTS3007R" if self.client.virtual else "TTTS3007R"
    headers = self.client.get_headers(tr_id)
    url = f"{self.client.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount"
    params = {
      "CANO": self.account_no,
      "ACNT_PRDT_CD": self.product_code,
      "OVRS_EXCG_CD": "NASD",
      "OVRS_ORD_UNPR": "1",
      "ITEM_CD": ref_ticker,
    }

    try:
      res = requests.get(url, headers=headers, params=params, timeout=10)
      body = res.json()
      if res.status_code == 200 and body.get('rt_cd') == '0':
        output = body['output']
        usd_cash = float(output.get('ord_psbl_frcr_amt') or 0)
        orderable = float(output.get('ovrs_ord_psbl_amt') or usd_cash)
        return {
          'total_cash': usd_cash,
          'available_cash': orderable,
          'exchange_rate': float(output.get('exrt') or 0),
        }
      print(f"❌ 잔고 조회 실패: HTTP {res.status_code} {body.get('msg1', '').strip()}")
    except Exception as e:
      print(f"❌ 잔고 조회 오류: {e}")

    return None

  # ============================================================
  # 보유 종목 조회
  # ============================================================

  def get_positions(self) -> Optional[List[Dict[str, Any]]]:
    """미국 주식 보유 종목 조회 (해외주식 잔고 API, 페이지네이션 처리)

    Returns:
        [{'ticker', 'shares', 'avg_price', 'current_price', 'exchange'}, ...]
        None → API 호출 실패
    """
    tr_id = "VTTS3012R" if self.client.virtual else "TTTS3012R"
    url = f"{self.client.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"

    positions: List[Dict[str, Any]] = []
    fk, nk = "", ""
    for _ in range(20):  # 무한 루프 방지
      headers = self.client.get_headers(tr_id)
      params = {
        "CANO": self.account_no,
        "ACNT_PRDT_CD": self.product_code,
        "OVRS_EXCG_CD": "NASD",   # 미국 전체 (NASD/NYSE/AMEX)
        "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": fk,
        "CTX_AREA_NK200": nk,
      }
      try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        body = res.json()
      except Exception as e:
        print(f"❌ 보유 종목 조회 오류: {e}")
        return None

      if res.status_code != 200 or body.get('rt_cd') != '0':
        print(f"❌ 보유 종목 조회 실패: HTTP {res.status_code} {body.get('msg1', '')}")
        return None

      for row in body.get('output1', []):
        shares = int(float(row.get('ovrs_cblc_qty') or 0))
        if shares <= 0:
          continue
        positions.append({
          'ticker': row.get('ovrs_pdno', '').strip(),
          'shares': shares,
          'avg_price': float(row.get('pchs_avg_pric') or 0),
          'current_price': float(row.get('now_pric2') or 0),
          'exchange': row.get('ovrs_excg_cd', ''),
        })

      # 연속 조회: tr_cont 가 F/M 이면 다음 페이지
      if res.headers.get('tr_cont') in ('F', 'M'):
        fk, nk = body.get('ctx_area_fk200', ''), body.get('ctx_area_nk200', '')
        continue
      break

    return positions

  # ============================================================
  # 매수
  # ============================================================

  def buy_stock(self, ticker: str, quantity: int, dry_run: bool = True) -> Dict[str, Any]:
    """미국 주식 매수"""
    if dry_run:
      return {
        'success': True,
        'message': f"[DRY RUN] {ticker} 매수 {quantity}주"
      }

    tr_id = "VTTT1002U" if self.client.virtual else "TTTT1002U"
    headers = self.client.get_headers(tr_id)
    url = f"{self.client.base_url}/uapi/overseas-stock/v1/trading/order"

    data = {
      "CANO": self.account_no,
      "ACNT_PRDT_CD": self.product_code,
      "OVRS_EXCG_CD": "NASD",
      "PDNO": ticker,
      "ORD_DVSN": "00",  # 지정가 (0으로 하면 시장가 비슷)
      "ORD_QTY": str(quantity),
      "OVRS_ORD_UNPR": "0",
      "ORD_SVR_DVSN_CD": "0"
    }

    try:
      res = requests.post(url, headers=headers, data=json.dumps(data),
                          timeout=10)
      result = res.json()
      return {
        'success': result.get('rt_cd') == '0',
        'message': result.get('msg1', ''),
        'order_no': result.get('output', {}).get('ODNO', '')
      }
    except Exception as e:
      return {'success': False, 'message': str(e)}

  # ============================================================
  # 매도
  # ============================================================

  def sell_stock(self, ticker: str, quantity: int, dry_run: bool = True) -> Dict[str, Any]:
    """미국 주식 매도"""
    if dry_run:
      return {
        'success': True,
        'message': f"[DRY RUN] {ticker} 매도 {quantity}주"
      }

    tr_id = "VTTT1001U" if self.client.virtual else "TTTT1001U"
    headers = self.client.get_headers(tr_id)
    url = f"{self.client.base_url}/uapi/overseas-stock/v1/trading/order"

    data = {
      "CANO": self.account_no,
      "ACNT_PRDT_CD": self.product_code,
      "OVRS_EXCG_CD": "NASD",
      "PDNO": ticker,
      "ORD_DVSN": "00",
      "ORD_QTY": str(quantity),
      "OVRS_ORD_UNPR": "0",
      "ORD_SVR_DVSN_CD": "0"
    }

    try:
      res = requests.post(url, headers=headers, data=json.dumps(data),
                          timeout=10)
      result = res.json()
      return {
        'success': result.get('rt_cd') == '0',
        'message': result.get('msg1', ''),
        'order_no': result.get('output', {}).get('ODNO', '')
      }
    except Exception as e:
      return {'success': False, 'message': str(e)}


# 테스트
if __name__ == "__main__":
  print("=" * 70)
  print("KIS Trader 테스트 (미국 주식)")
  print("=" * 70)

  try:
    trader = KISTrader()

    # 잔고 조회
    balance = trader.get_balance()
    if balance is None:
      print("\n잔고 조회 실패")
    else:
      print(f"\n잔고: ${balance['total_cash']:,.2f}")

    positions = trader.get_positions()
    print(f"보유 종목: {positions}")

    # DRY RUN 매수
    result = trader.buy_stock("NVDA", 10, dry_run=True)
    print(f"매수 테스트: {result['message']}")

    # DRY RUN 매도
    result = trader.sell_stock("NVDA", 10, dry_run=True)
    print(f"매도 테스트: {result['message']}")

  except Exception as e:
    print(f"❌ 오류: {e}")
