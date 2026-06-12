from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings


class RAG:
    def __init__(self, vector_db_path, embedding_type='openai'):
        self.vector_db_path = str(vector_db_path)

        if embedding_type == 'openai':
            self.embeddings_model = OpenAIEmbeddings(model="text-embedding-3-large")
        elif embedding_type == 'huggingface':
            # Optional dependency: pip install langchain-huggingface
            from langchain_huggingface import HuggingFaceEmbeddings
            self.embeddings_model = HuggingFaceEmbeddings(model_name="intfloat/e5-base-v2")
        else:
            raise ValueError("Invalid embedding type. Choose 'openai' or 'huggingface'.")

    def _vector_store(self):
        return Chroma(embedding_function=self.embeddings_model, persist_directory=self.vector_db_path)

    def create_embeddings_and_store(self, summary_md_path):
        path = Path(summary_md_path)
        document = Document(page_content=path.read_text(encoding="utf-8"), metadata={"source": str(path)})

        vector_store = self._vector_store()
        # Chroma persists automatically; the old explicit persist() call is gone.
        vector_store.add_documents([document])
        return vector_store

    def retrieve_embeddings(self, query, n=10, threshold=0.15):
        vector_store = self._vector_store()

        # Retrieve the top n relevant embeddings with scores
        results_with_scores = vector_store.similarity_search_with_score(query, k=n)
        if not results_with_scores:
            return []

        first_result_score = results_with_scores[0][1]

        # Keep results whose score is within `threshold` of the best score
        filtered_results = [
            result
            for result, score in results_with_scores
            if first_result_score * (1 - threshold) <= score <= first_result_score * (1 + threshold)
        ]

        return filtered_results
