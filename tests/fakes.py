"""Dublês de Supabase para testes locais, sem acesso à base real."""
from unittest.mock import MagicMock

CHAVE = "35240112345678000199550010000012341000012345"

NFE_XML = f'''<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe{CHAVE}" versao="4.00">
      <ide><serie>1</serie><nNF>1234</nNF><natOp>VENDA</natOp><dhEmi>2024-01-15T10:30:00-03:00</dhEmi></ide>
      <emit><CNPJ>12345678000199</CNPJ><xNome>Fornecedor LTDA</xNome><xFant>Forn Semijoias</xFant><enderEmit><xMun>Limeira</xMun><UF>SP</UF></enderEmit></emit>
      <det nItem="1"><prod><cProd>A001</cProd><xProd>Brinco Argola</xProd><NCM>71171900</NCM><uCom>UN</uCom><qCom>10.0000</qCom><vUnCom>5.5000</vUnCom><vProd>55.00</vProd><vDesc>5.00</vDesc></prod></det>
      <det nItem="2"><prod><cProd>B002</cProd><xProd>Colar Ponto de Luz</xProd><NCM>71171900</NCM><uCom>UN</uCom><qCom>2.0000</qCom><vUnCom>20.0000</vUnCom><vProd>40.00</vProd></prod></det>
      <total><ICMSTot><vProd>95.00</vProd><vDesc>5.00</vDesc><vNF>90.00</vNF></ICMSTot></total>
    </infNFe>
  </NFe>
  <protNFe><infProt><chNFe>{CHAVE}</chNFe></infProt></protNFe>
</nfeProc>'''


class Response:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, sb, table):
        self.sb = sb
        self.table_name = table
        self.op = "select"
        self.payload = None
        self.kwargs = {}
        self.filters = []

    def select(self, *args, **kwargs):
        self.op = "select"
        self.kwargs = kwargs
        return self

    def insert(self, payload, **kwargs):
        self.op = "insert"
        self.payload = payload
        self.kwargs = kwargs
        return self

    def upsert(self, payload, **kwargs):
        self.op = "upsert"
        self.payload = payload
        self.kwargs = kwargs
        return self

    def update(self, payload, **kwargs):
        self.op = "update"
        self.payload = payload
        self.kwargs = kwargs
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _f(self, name, *args):
        self.filters.append((name, args))
        return self

    def eq(self, *args): return self._f("eq", *args)
    def neq(self, *args): return self._f("neq", *args)
    def is_(self, *args): return self._f("is_", *args)
    def in_(self, *args): return self._f("in_", *args)
    def ilike(self, *args): return self._f("ilike", *args)
    def gte(self, *args): return self._f("gte", *args)
    def gt(self, *args): return self._f("gt", *args)
    def lte(self, *args): return self._f("lte", *args)
    def lt(self, *args): return self._f("lt", *args)
    def order(self, *args, **kwargs): return self._f("order", *args)
    def range(self, *args): return self._f("range", *args)
    def limit(self, *args): return self._f("limit", *args)

    def execute(self):
        self.sb.calls.append(self)
        response = self.sb.responses.get((self.table_name, self.op))
        if callable(response):
            response = response(self)
        if response is None:
            response = Response([] if self.op == "select" else [{"id": self.sb.next_id()}])
        elif isinstance(response, list):
            response = Response(response, count=len(response))
        return response


class FakeSupabase:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.auth = MagicMock()
        self._id = 100

    def next_id(self):
        self._id += 1
        return self._id

    def table(self, nome):
        return FakeQuery(self, nome)

    def calls_for(self, tabela, op=None):
        return [c for c in self.calls if c.table_name == tabela and (op is None or c.op == op)]

    def last_call(self, tabela, op=None):
        chamadas = self.calls_for(tabela, op)
        assert chamadas, f"Nenhuma chamada encontrada para {tabela}/{op}"
        return chamadas[-1]
