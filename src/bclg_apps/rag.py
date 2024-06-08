import os
from langchain_community.document_loaders import UnstructuredMarkdownLoader
from langchain_community.embeddings import OpenAIEmbeddings, GPT4AllEmbeddings
from langchain_community.vectorstores import Chroma

class RAG:
    def __init__(self, vector_db_path, use_openai_embeddings=True):
        self.vector_db_path = str(vector_db_path)
        if use_openai_embeddings:
            self.embeddings_model = OpenAIEmbeddings()
        else:
            model_name = "all-MiniLM-L6-v2.gguf2.f16.gguf"
            gpt4all_kwargs = {'allow_download': 'True'}
            self.embeddings_model = GPT4AllEmbeddings(
                model_name=model_name,
                gpt4all_kwargs=gpt4all_kwargs)
    
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

    def retrieve_embeddings(self, query, n=5):
        # Load the existing vector store
        vector_store = Chroma(embedding_function=self.embeddings_model, persist_directory=self.vector_db_path)

        # Retrieve the top n relevant embeddings
        results = vector_store.similarity_search(query, k=n)

        return results

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
    results = rag.retrieve_embeddings(query, n=5)

    for result in results:
        print(result)
        