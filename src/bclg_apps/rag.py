import os
from langchain_community.document_loaders import UnstructuredMarkdownLoader
from langchain_openai import OpenAIEmbeddings
from langchain_community.embeddings import GPT4AllEmbeddings, HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

class RAG:
    def __init__(self, vector_db_path, embedding_type='openai'):
        self.vector_db_path = str(vector_db_path)
        
        if embedding_type == 'openai':
            self.embeddings_model = OpenAIEmbeddings(model="text-embedding-3-large")
        elif embedding_type == 'gpt4all':
            model_name = "all-MiniLM-L6-v2.gguf2.f16.gguf"
            gpt4all_kwargs = {'allow_download': True}
            self.embeddings_model = GPT4AllEmbeddings(
                model_name=model_name,
                gpt4all_kwargs=gpt4all_kwargs)
        elif embedding_type == 'huggingface':
            self.embeddings_model = HuggingFaceEmbeddings(model_name="intfloat/e5-base-v2")
        else:
            raise ValueError("Invalid embedding type. Choose 'openai', 'gpt4all', or 'huggingface'.")
    
    def create_embeddings_and_store(self, summary_md_path):
        # Load the markdown summary
        loader = UnstructuredMarkdownLoader(str(summary_md_path))
        documents = loader.load()

        # Initialize the Chroma vector store
        vector_store = Chroma(embedding_function=self.embeddings_model, persist_directory=self.vector_db_path)

        # Create and store embeddings
        vector_store.add_documents(documents)

        # Persist the vector store
        vector_store.persist()

        return vector_store

    def retrieve_embeddings(self, query, n=10, threshold=0.15):
        # Load the existing vector store
        vector_store = Chroma(embedding_function=self.embeddings_model, persist_directory=self.vector_db_path)

        # Retrieve the top n relevant embeddings with scores
        results_with_scores = vector_store.similarity_search_with_score(query, k=n)

        first_result_score = results_with_scores[0][1]

        # Filter results based on the threshold score
        filtered_results = [result for result, score in results_with_scores if (score >= first_result_score * (1-threshold) and score <= first_result_score * (1+threshold))]

        return filtered_results

# Example usage
if __name__ == "__main__":
    summary_md = """
    # Example Summary
    This is an example summary in markdown format.
    """

    vector_db_path = "path_to_vector_db"

    # Initialize RAG with OpenAI embeddings
    rag = RAG(vector_db_path=vector_db_path, use_openai_embeddings=True)

    # Create embeddings and store them
    rag.create_embeddings_and_store(summary_md)

    # Retrieve embeddings for a user query
    query = "example query"
    results = rag.retrieve_embeddings(query, n=5, threshold=0.7)

    for result in results:
        print(result)
