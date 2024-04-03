"""
Jesus's Rock,Paper, Scissors Game
12/06/23
"""
import random

def win(user_action, computer_action):
    if user_action == computer_action:
        return (f"Both players selected {user_action}. It's a tie!")
    elif user_action == "rock":
        if computer_action == "scissors":
            return("Rock SMASHES Scissors! YOU WON!;)")
        else:
            return("Paper COVERS Rock! YOU LOST!:(")
    elif user_action == "paper":
        if computer_action == "rock":
            return("Paper COVERS Rock! YOU WON!;)")
        else:
            return("Scissors SLASHES PAPER! YOU LOST!:(")
    elif user_action == "scissors":
        if computer_action == "paper":
            return("Scissors SLASHES paper! YOU WON!;)")
        else:
            return("Rock SMASHES Scissors! YOU LOST!:(")

def main():
    while True:
        user_action = input("SELECT A WEAPON (ROCK, PAPER, SCISSORS): ")
        possible_actions: list[str] = ["Rock", "Paper", "Scissors"]
        computer_action = random.choice(possible_actions)
        print(f"\nYou chose {user_action}, computer chose {computer_action}.\n")
        if user_action != 'rock' or user_action != 'paper' or user_action != 'scissors':
            print("Invalid Input, Please Enter 'Rock, Paper, or Scissors'")
        else:
            print(win(computer_action, user_action))
            play_again = input("WOULD YOU LIKE TO PLAY AGAIN? (y/n): ")
            if play_again.lower() != "y":
                    break

if __name__ == "__main__":
    main()
