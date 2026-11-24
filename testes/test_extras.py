import unittest

from simes import parametros as P
from simes.arquivos.estruturas import ErroSA
from simes.arquivos.simulador import montar_sistema
from simes.disco import DiscoFlash
from simes.es.buffering import simular_buffering
from simes.es.cache import CacheFIFO, CacheLRU
from simes.es.escalonadores import SCAN, SSTF, percorrer


class TesteFlash(unittest.TestCase):
    def test_leitura_e_escrita_sem_coleta(self):
        f = DiscoFlash(total_blocos_logicos=1024)
        t, dist = f.atender(10, 4, "L")
        self.assertEqual(dist, 0)
        self.assertAlmostEqual(t, 4 * P.FLASH_LEITURA_MS)
        t, _ = f.atender(10, 4, "E")
        self.assertAlmostEqual(t, 4 * P.FLASH_PROGRAMACAO_MS)
        self.assertEqual((f.paginas_gravadas_host, f.paginas_programadas, f.apagamentos), (4, 4, 0))

    def test_regravacao_gera_coleta_e_amplificacao(self):
        f = DiscoFlash(total_blocos_logicos=256)
        for b in range(256):
            f.atender(b, 1, "E")
        for rodada in range(20):
            for b in range(0, 256, 3):
                f.atender(b, 1, "E")
        self.assertGreater(f.apagamentos, 0)
        self.assertGreaterEqual(f.paginas_programadas, f.paginas_gravadas_host)
        self.assertGreater(f.amplificacao_escrita, 1.0)
        for logico, pagina in enumerate(f.mapa):
            self.assertEqual(f.reverso[pagina], logico)
        self.assertEqual(sum(f.validas), 256)


class TesteBordas(unittest.TestCase):
    def test_sstf_empate_vence_o_mais_antigo(self):
        ordem, _ = percorrer(SSTF(), [60, 40], 50)
        self.assertEqual(ordem, [60, 40])

    def test_scan_na_borda_nao_repete_movimento(self):
        ordem, total = percorrer(SCAN(), [10, 5], 199, direcao=+1)
        self.assertEqual(ordem, [10, 5])
        self.assertEqual(total, 194)

    def test_fifo_escrita_nao_reordena(self):
        c = CacheFIFO(2, "write-back")
        c.inserir(1)
        c.inserir(2)
        c.escrever(1)
        self.assertEqual(c.inserir(3), [1])

    def test_lru_escrita_conta_como_acesso(self):
        c = CacheLRU(2, "write-back")
        c.inserir(1)
        c.inserir(2)
        c.escrever(1)
        self.assertEqual(c.inserir(3), [])
        self.assertTrue(c.ler(1))

    def test_buffering_circular(self):
        r = simular_buffering(3, 2.0, 0.5, 10)
        self.assertAlmostEqual(r["tempo_total_ms"], 10 * 2.0 + 0.5)
        self.assertAlmostEqual(r["bloqueio_produtor_ms"], 0.0)

    def test_open_handle_repetido_e_modo_invalido(self):
        sa = montar_sistema("contigua", total_blocos=1000)
        sa.create("P1", "/f", 100)
        sa.open("P1", "/f", "r", "h")
        with self.assertRaises(ErroSA) as ctx:
            sa.open("P1", "/f", "r", "h")
        self.assertEqual(ctx.exception.codigo, "EBADF")
        with self.assertRaises(ErroSA) as ctx:
            sa.open("P1", "/f", "x", "h2")
        self.assertEqual(ctx.exception.codigo, "EINVAL")

    def test_custos_com_alocador_indexado(self):
        sa = montar_sistema("indexada", total_blocos=5000)
        c = sa.camada
        antes = (c.leituras, c.escritas)
        sa.mkdir("P1", "/d")
        self.assertEqual((c.leituras - antes[0], c.escritas - antes[1]), (4, 3))

    def test_alocacao_sem_espaco_desfaz_o_fcb(self):
        sa = montar_sistema("contigua", total_blocos=10)
        n = len(sa.fcbs)
        with self.assertRaises(ErroSA) as ctx:
            sa.create("P1", "/grande", 100 * 4096)
        self.assertEqual(ctx.exception.codigo, "ENOSPC")
        self.assertEqual(len(sa.fcbs), n)
        self.assertNotIn("grande", sa.raiz.entradas)


if __name__ == "__main__":
    unittest.main()
