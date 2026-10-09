"""
DeskCortex — Catálogo de Tutoriais Oficiais em Vídeo e Mídias (ERP Master)
========================================================================
Base de conhecimento oficial de vídeos do YouTube e animações/GIFs do ERP Master
da Alternativa Sistemas para autoatendimento guiado de clientes.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import re


@dataclass
class TutorialGuide:
    id: str
    topic: str
    video_title: str
    video_url: str
    media_path: Optional[str] = None
    media_title: Optional[str] = None
    media_type: str = "gif"
    screen: str = ""
    shortcuts: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    guide_text: str = ""


TUTORIAL_CATALOG: Dict[str, TutorialGuide] = {
    "importacao_planilha": TutorialGuide(
        id="importacao_planilha",
        topic="Importação de Produtos / Atualização via Planilha Excel",
        video_title="Importação de Produtos via Planilha no ERP Master",
        video_url="https://www.youtube.com/watch?v=M4HzqQ6ekl4",
        media_path="Produtos/alterando produtos/alt. produto/alterando produto em massa.gif",
        media_title="Demonstração: Carga de Planilha no Master",
        screen="Cadastros > Estoque / Produtos > Atualização de Produtos",
        shortcuts=["F6 (Carregar Planilha)", "F4 (Confirmar)"],
        keywords=[
            "planilha", "excel", "xls", "importar produto", "importar planilha",
            "importa planilha", "importacao da planilha", "importação da planilha",
            "importação de planilha", "carga massiva", "em massa", "atualizacao em massa",
            "atualização em massa", "subir planilha", "subir produtos"
        ],
        guide_text=(
            "A importação de produtos via planilha no ERP Master é muito prática para cadastrar ou atualizar itens em lote! 📊\n\n"
            "**1. Formato obrigatório da planilha:**\n"
            "• Salve o arquivo em formato **XLS (Pasta de Trabalho do Excel 97-2003)**.\n"
            "• O arquivo deve ter apenas **1 aba** (remova quaisquer sub-abas).\n"
            "• Não utilize fórmulas ou filtros.\n"
            "• Limite recomendado: até 10.000 produtos por arquivo.\n"
            "• Coluna essencial: `PRO_CODIGO` (código do produto que serve como chave de busca/WHERE), além das colunas que deseja atualizar (ex: Descrição, Preço, NCM, Estoque).\n\n"
            "**2. Passo a passo no ERP Master:**\n"
            "1. Acesse o menu: **Cadastros > Estoque / Produtos > Atualização de Produtos** (ou **Centro de Alteração de Dados**).\n"
            "2. Na tela, deixe marcado o campo **Código** e as colunas que você deseja carregar da planilha.\n"
            "3. Clique no botão **Carregar Planilha** (ou use a tecla de atalho **F6**).\n"
            "4. Selecione o arquivo `.xls` no seu computador.\n"
            "5. O Master vai listar os itens. Confira a coluna **'Encontrou?'** — certifique-se de que não haja itens marcados com **'NÃO'**.\n"
            "6. Tudo conferido? Clique no botão **Confirmar** (ou atalho **F4**) para processar a carga!\n\n"
            "🎥 **Vídeo Tutorial Oficial Alternativa Sistemas:**\n"
            "Você pode acompanhar o clique a clique na tela neste vídeo explicativo:\n"
            "👉 [Assistir: Importação de Produtos via Planilha no YouTube](https://www.youtube.com/watch?v=M4HzqQ6ekl4)\n\n"
            "Dica: Também deixei aqui embaixo uma animação da tela para você ver como funciona. Quer ajuda com o modelo das colunas da planilha?"
        )
    ),

    "cadastro_produto": TutorialGuide(
        id="cadastro_produto",
        topic="Cadastro Individual de Produtos no ERP Master",
        video_title="Cadastro de Produto - Alternativa Sistemas",
        video_url="https://www.youtube.com/watch?v=2arHOvvg1Ns",
        media_path="Produtos/cadastrando produto/cadastro de produtos.gif",
        media_title="Passo a passo: Tela de Cadastro de Produtos",
        screen="Cadastros > Estoque / Produtos",
        shortcuts=["F3 (Pesquisar)", "Insert (Novo)", "F4 (Salvar)"],
        keywords=[
            "cadastrar produto", "cadastra produto", "cadastrar produtos", "cadastro de produto",
            "cadastro de produtos", "cadastrar um produto", "cadastrar um prroduto",
            "criar produto", "novo produto", "incluir produto"
        ],
        guide_text=(
            "Com certeza, parceiro! Cadastrar um produto no ERP Master é super simples e rápido: 📦\n\n"
            "**Passo a passo no Master:**\n"
            "1. No menu superior, acesse: **Cadastros > Estoque / Produtos** (ou utilize o atalho de pesquisa rápida **F3**).\n"
            "2. Clique no botão **Novo** (ou pressione a tecla **Insert** no teclado).\n"
            "3. Preencha as informações cadastrais básicas:\n"
            "   • **Código / Referência:** Código interno ou SKU do item.\n"
            "   • **Descrição:** Nome comercial completo do produto.\n"
            "   • **Unidade:** Unidade de medida (ex: UN, PC, CX, KG).\n"
            "   • **Preço de Venda:** Valor para tabelas de venda e faturamento.\n"
            "4. Na aba **Fiscal / Tributação**, preencha os dados tributários:\n"
            "   • **NCM:** Código de 8 dígitos da classificação fiscal.\n"
            "   • **Origem:** (ex: 0 - Nacional, 1 - Importação direta).\n"
            "   • **CST / CSOSN:** Conforme o enquadramento fiscal da sua empresa.\n"
            "5. Clique no botão **Salvar** (ou atalho **F4**).\n\n"
            "🎥 **Vídeo Tutorial Oficial Alternativa Sistemas:**\n"
            "Preparamos um vídeo rápido mostrando todos os campos na prática:\n"
            "👉 [Assistir: Tutorial de Cadastro de Produtos no YouTube](https://www.youtube.com/watch?v=2arHOvvg1Ns)\n\n"
            "Pronto! O produto já fica ativo no sistema para vendas, estoque e emissão de notas fiscais. Quer ajuda com algum campo específico do cadastro?"
        )
    ),

    "grades_produtos": TutorialGuide(
        id="grades_produtos",
        topic="Cadastro de Produtos com Grade (Variações / Cores / Tamanhos)",
        video_title="Cadastro de Grade X e Y - Alternativa Sistemas",
        video_url="https://www.youtube.com/watch?v=7RTQG4C6Z8o",
        media_path="cadastro/cadastro-fabricantes.gif",
        media_title="Estrutura de Grades e Variações",
        screen="Cadastros > Estoque / Produtos > aba Grades",
        shortcuts=["F3 (Pesquisar)", "F4 (Salvar)"],
        keywords=[
            "grade", "grades", "variacao", "variacoes", "variação", "variações",
            "produto pai", "filho", "tamanho", "cor", "matriz de grade", "grade x e y"
        ],
        guide_text=(
            "Trabalhar com grade no ERP Master é excelente para itens que possuem variações de tamanho, cor ou voltagem! 🏷️\n\n"
            "**Como funciona a estrutura de Grades no Master:**\n"
            "1. Cadastre o **Produto Pai** (que concentra as informações comuns: Descrição principal, Categoria, Marca e NCM).\n"
            "2. Acesse a aba **Grades** dentro do cadastro do produto.\n"
            "3. Configure os eixos da matriz:\n"
            "   • **Eixo X:** Variação principal (ex: Tamanhos P, M, G, GG).\n"
            "   • **Eixo Y:** Variação secundária (ex: Cores Preto, Azul, Vermelho).\n"
            "4. O Master vai gerar automaticamente os **Produtos Filhos**, cada um com seu código de barras (EAN), SKU e controle de estoque individual.\n"
            "5. Clique em **Salvar (F4)**.\n\n"
            "🎥 **Vídeo Tutorial Oficial Alternativa Sistemas:**\n"
            "Veja como montar a matriz de grade passo a passo:\n"
            "👉 [Assistir: Cadastro de Grade X e Y no YouTube](https://www.youtube.com/watch?v=7RTQG4C6Z8o)\n"
            "E se for cadastrar grade via planilha: [Tutorial Planilha com Grade](https://www.youtube.com/watch?v=qRCAWsn7d7s)\n\n"
            "Quer que eu te oriente na configuração de algum eixo de grade específico?"
        )
    ),

    "visualizacao_estoque": TutorialGuide(
        id="visualizacao_estoque",
        topic="Consulta e Controle de Estoque / Inventário / Transferência",
        video_title="Transferência e Controle de Estoque - Alternativa Sistemas",
        video_url="https://www.youtube.com/watch?v=3MhL_OQQOBA",
        media_path="cadastro/unidade de estoque.gif",
        media_title="Consulta de Estoque e Movimentação",
        screen="Estoque > Visualização de Estoque (ou Gerenciamento > Produtos)",
        shortcuts=["F3 (Pesquisar Produto)"],
        keywords=[
            "ver estoque", "olhar estoque", "consultar estoque", "onde vejo estoque",
            "estoque", "saldo", "inventario", "inventário", "transferencia de estoque",
            "transferência de estoque", "acerto de estoque", "dar baixa"
        ],
        guide_text=(
            "Para consultar e movimentar o estoque no ERP Master, você tem caminhos bem práticos: 📦\n\n"
            "**1. Para apenas consultar saldo e movimentações:**\n"
            "• Acesse: **Cadastros > Estoque / Produtos** (atalho **F3**), localize o item e abra a aba **Estoque / Kardex**.\n"
            "• Ou acesse o menu rápido: **Estoque > Visualização de Estoque**.\n\n"
            "**2. Para fazer inventário ou acerto de saldo:**\n"
            "• Acesse: **Estoque > Inventário / Acerto de Estoque**.\n"
            "• Informe o produto, selecione o depósito/almoxarifado e digite a quantidade contada real.\n\n"
            "**3. Para transferir produtos entre almoxarifados ou filiais:**\n"
            "• Acesse: **Estoque > Transferência entre Localizações**.\n\n"
            "🎥 **Vídeos Tutoriais Oficiais:**\n"
            "👉 [Assistir: Transferência de Estoque no YouTube](https://www.youtube.com/watch?v=3MhL_OQQOBA)\n"
            "👉 [Assistir: Inventário de Estoque - Vantagens e Facilidades](https://www.youtube.com/watch?v=9To30TdrgKU)\n\n"
            "Precisa de ajuda com alguma divergência ou transferência específica?"
        )
    ),

    "realizar_venda": TutorialGuide(
        id="realizar_venda",
        topic="Como Realizar Vendas (Balcão / Pedidos / Faturamento)",
        video_title="Como Realizar Venda de Balcão - Alternativa Sistemas",
        video_url="https://www.youtube.com/watch?v=JlqAOSObJc0",
        media_path="Tributação/GRUPO DE TRIBUTAÇAO.gif",
        media_title="Tela de Vendas e Faturamento",
        screen="Vendas > Frente de Caixa (ou Gerenciamento > Pedidos)",
        shortcuts=["F3 (Pesquisar Itens/Clientes)", "F4 (Finalizar Venda)"],
        keywords=[
            "realizar venda", "fazer venda", "venda balcao", "venda balcão",
            "como vender", "lancar venda", "lançar venda", "faturar pedido",
            "gerenciamento de pedidos", "tirar pedido"
        ],
        guide_text=(
            "Fazer uma venda no ERP Master é muito ágil! O processo varia conforme o canal: 🛍️\n\n"
            "**1. Venda Rápida / Balcão (Frente de Loja):**\n"
            "• Acesse o menu: **Vendas > Frente de Caixa / Venda Balcão**.\n"
            "• Digite o código do produto ou passe o leitor de código de barras.\n"
            "• Se precisar buscar por nome, use o atalho **F3**.\n"
            "• Informe a forma de pagamento (Dinheiro, Cartão, PIX) e pressione **F4** para concluir.\n\n"
            "**2. Venda de Pedidos / E-commerce:**\n"
            "• Acesse: **Gerenciamento > Pedidos**.\n"
            "• Localize o pedido recebido do marketplace ou loja virtual.\n"
            "• Clique no botão **Faturar** para gerar a nota e o envio.\n\n"
            "🎥 **Vídeos Tutoriais Oficiais:**\n"
            "👉 [Assistir: Como realizar venda de balcão no YouTube](https://www.youtube.com/watch?v=JlqAOSObJc0)\n"
            "👉 [Assistir: Vendas Gerenciamento - Visão Geral](https://www.youtube.com/watch?v=jhHcbex9obk)\n\n"
            "Ficou alguma dúvida sobre o faturamento ou pagamento da venda?"
        )
    ),

    "cadastro_cliente": TutorialGuide(
        id="cadastro_cliente",
        topic="Cadastro de Clientes e Fornecedores",
        video_title="Cadastro de Clientes - Alternativa Sistemas",
        video_url="https://www.youtube.com/watch?v=LgkokvuQwPQ",
        media_path="cadastro/clientes.gif",
        media_title="Cadastro de Clientes e Fornecedores",
        screen="Cadastros > Clientes e Fornecedores",
        shortcuts=["Insert (Novo)", "F4 (Salvar)"],
        keywords=[
            "cadastrar cliente", "cadastra cliente", "cadastro de cliente", "cadastro de clientes",
            "cadastrar fornecedor", "cadastro fornecedor", "novo cliente", "incluir cliente"
        ],
        guide_text=(
            "Cadastrar cliente ou fornecedor no ERP Master é simples: 👥\n\n"
            "**Passo a passo no Master:**\n"
            "1. No menu superior, acesse: **Cadastros > Clientes e Fornecedores**.\n"
            "2. Clique no botão **Novo** (ou aperte a tecla **Insert** no teclado).\n"
            "3. Escolha o tipo: **Pessoa Física (CPF)** ou **Pessoa Jurídica (CNPJ)**.\n"
            "4. Preencha Razão Social / Nome, Nome Fantasia, Endereço completo com CEP e Telefone.\n"
            "5. Se for empresa contribuinte de ICMS, preencha a **Inscrição Estadual (IE)**.\n"
            "6. Clique em **Salvar (F4)**.\n\n"
            "🎥 **Vídeo Tutorial Oficial Alternativa Sistemas:**\n"
            "👉 [Assistir: Cadastro de Clientes no YouTube](https://www.youtube.com/watch?v=LgkokvuQwPQ)\n"
            "👉 [Assistir: Cadastro de Fornecedor no YouTube](https://www.youtube.com/watch?v=B0uQsv6tTc8)\n\n"
            "Já fica disponível imediatamente para emitir notas e vendas! Precisa de mais algum detalhe?"
        )
    ),

    "emissao_nfe": TutorialGuide(
        id="emissao_nfe",
        topic="Emissão de NF-e, Tributação e Inutilização",
        video_title="Cadastro de Tributação e Emissão de NF-e - Alternativa Sistemas",
        video_url="https://www.youtube.com/watch?v=K1KPvsozqWI",
        media_path="Tributação/Natureza de operaçao.gif",
        media_title="Emissão e Tributação de NF-e",
        screen="Vendas > Faturamento > NF-e",
        shortcuts=["F3 (Pesquisar)", "F4 (Salvar/Transmitir)"],
        keywords=[
            "emitir nota", "emite nota", "emitir nfe", "emissao de nota", "emissão de nota",
            "como emitir nota fiscal", "como tirar nota", "inutilizacao", "inutilização",
            "carta de correcao", "carta de correção", "tributacao", "tributação"
        ],
        guide_text=(
            "Para emitir NF-e no ERP Master: 🧾\n\n"
            "1. Se a venda já estiver em pedido: acesse **Gerenciamento > Pedidos**, localize a venda e clique no botão **Faturar**.\n"
            "2. Ou vá direto no menu: **Vendas > Faturamento > NF-e** e clique em **Nova Nota**.\n"
            "3. Selecione o destinatário e confira os itens e quantidades.\n"
            "4. Confira o **CFOP** e a Natureza de Operação na aba Tributação.\n"
            "5. Clique em **Validar / Transmitir** para enviar à SEFAZ.\n\n"
            "🎥 **Vídeos Tutoriais Oficiais:**\n"
            "👉 [Assistir: Cadastro de Tributação no YouTube](https://www.youtube.com/watch?v=K1KPvsozqWI)\n"
            "👉 [Assistir: Grupo de Tributação no YouTube](https://www.youtube.com/watch?v=UG6ioUvoEU4)\n"
            "👉 [Assistir: Inutilização de NF-e no YouTube](https://www.youtube.com/watch?v=0lZh0eVZk2c)\n"
            "👉 [Assistir: Carta de Correção de NF-e no YouTube](https://www.youtube.com/watch?v=zqvY54_svro)\n\n"
            "Assim que a SEFAZ autorizar, a DANFE já fica liberada para impressão!"
        )
    ),

    "centro_alteracao_dados": TutorialGuide(
        id="centro_alteracao_dados",
        topic="Centro de Alteração de Dados (Edição em Massa)",
        video_title="Centro de Alteração de Dados - Alternativa Sistemas",
        video_url="https://www.youtube.com/watch?v=6oUk751LMeY",
        media_path="Produtos/alterando produtos/alt. produto/alterando produto em massa.gif",
        media_title="Centro de Alteração de Dados",
        screen="Configurações > Centro de Alteração de Dados",
        shortcuts=["F6 (Importar)", "F4 (Aplicar Alterações)"],
        keywords=[
            "centro de alteracao", "centro de alteração", "dicionario de dados", "dicionário de dados",
            "alterar em massa", "alterar vários produtos", "ajuste massivo"
        ],
        guide_text=(
            "O **Centro de Alteração de Dados** é uma ferramenta avançada do ERP Master para modificar registros em lote de forma muito rápida! ⚡\n\n"
            "**Como utilizar:**\n"
            "1. Acesse: **Configurações > Centro de Alteração de Dados**.\n"
            "2. Selecione a tabela que deseja alterar (ex: Produtos, Vendas, Clientes).\n"
            "3. Filtre os registros que você deseja modificar (por intervalo de códigos, grupo ou categoria).\n"
            "4. Você pode alterar os campos diretamente na grade ou exportar/importar via planilha.\n"
            "5. Após os ajustes, clique em **Aplicar Alterações**.\n\n"
            "🎥 **Vídeos Tutoriais Oficiais:**\n"
            "👉 [Assistir: Centro de Alteração de Dados no YouTube](https://www.youtube.com/watch?v=6oUk751LMeY)\n"
            "👉 [Assistir: Dicionário de Dados no YouTube](https://www.youtube.com/watch?v=BUImuNMVq3o)\n\n"
            "Precisa de ajuda com alguma tabela específica no Centro de Alterações?"
        )
    ),
}


def find_matching_tutorial(message: str) -> Optional[TutorialGuide]:
    """Localiza o melhor tutorial oficial compatível com a mensagem do cliente."""
    m = message.lower()
    
    # GUARDRAIL CRÍTICO: Se a mensagem relata ERRO, REJEIÇÃO ou FALHA técnica,
    # NUNCA retornar tutorial genérico de como fazer! O cliente precisa de diagnóstico.
    error_terms = [
        "erro", "rejei", "rejeição", "rejeicao", "falha", "travou", "travando",
        "bug", "problema", "nao consigo", "não consigo", "recusad", "nao autoriza",
        "não autoriza", "nao emite", "não emite", "nao salva", "não salva",
        "nao deixa", "não deixa", "violacao", "violação", "deu ruim", "exception"
    ]
    if any(t in m for t in error_terms):
        return None

    # 1. Checagem prioritária para Planilha / Excel
    if any(k in m for k in ["planilha", "excel", "xls", "em massa", "massivo", "carga"]):
        if any(w in m for w in ["produto", "estoque", "item", "cadastro", "importa", "como", "faco", "faço"]):
            return TUTORIAL_CATALOG["importacao_planilha"]
    
    # 2. Checagem para Grade
    if any(k in m for k in ["grade", "grades", "variacao", "variação", "filho", "pai"]):
        return TUTORIAL_CATALOG["grades_produtos"]

    # 3. Checagem para Estoque / Saldo / Inventário
    if any(k in m for k in ["estoque", "saldo", "inventario", "inventário", "transferencia", "transferência"]) and any(w in m for w in ["ver", "olhar", "onde", "como", "consultar"]):
        return TUTORIAL_CATALOG["visualizacao_estoque"]

    # 4. Checagem para Vendas
    if any(k in m for k in ["venda", "vendas", "vender", "balcao", "balcão", "frente de caixa"]):
        return TUTORIAL_CATALOG["realizar_venda"]

    # 5. Checagem para NF-e
    if any(k in m for k in ["nota", "nf", "nfe", "danfe"]) and any(w in m for w in ["emitir", "emite", "gerar", "como", "inutilizar", "carta"]):
        return TUTORIAL_CATALOG["emissao_nfe"]

    # 6. Checagem para Clientes / Fornecedores
    if any(k in m for k in ["cliente", "clientes", "fornecedor", "fornecedores"]) and any(w in m for w in ["cadastr", "inclu", "criar", "novo"]):
        return TUTORIAL_CATALOG["cadastro_cliente"]

    # 7. Checagem para Cadastro de Produto individual
    if any(k in m for k in ["produto", "produtos", "prroduto", "item"]) and any(w in m for w in ["cadastr", "inclu", "cria", "novo", "fazer"]):
        return TUTORIAL_CATALOG["cadastro_produto"]

    # 8. Varredura por keywords do catálogo
    for guide in TUTORIAL_CATALOG.values():
        if any(re.search(r'\b' + re.escape(kw) + r'\b', m) for kw in guide.keywords):
            return guide

    return None
