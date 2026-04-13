def main():
    fib_sequence = [0, 1]
    for _ in range(2, 20):
        next_num = fib_sequence[-1] + fib_sequence[-2]
        fib_sequence.append(next_num)
    for num in fib_sequence:
        print(num)

if __name__ == "__main__":
    main()
