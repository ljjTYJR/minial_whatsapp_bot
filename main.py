# THe main structure of the project
from model import OpenRouterModel
import logging

def setup_model(log_level=logging.DEBUG):
    model = OpenRouterModel(log_level=log_level)
    return model

def main():
    model = setup_model(log_level=logging.DEBUG)  # Use DEBUG to see all messages, INFO to hide debug

    messages = []
    # the main agnet loop
    while True:
        # get the user input
        user_input = input("User: ")
        messages.append({"role": "user", "content": user_input})
        response = model.generate_response(messages)
        messages.append({"role": "assistant", "content": response})
        print("Bot: ", response)

if __name__ == "__main__":
    main()