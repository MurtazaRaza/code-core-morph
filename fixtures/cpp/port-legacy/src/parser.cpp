#include "calc/parser.h"

#include "calc/lexer.h"
#include "stack.h"

namespace calc {

ParseError::ParseError(const std::string& message, std::size_t pos)
    : std::runtime_error(message), pos_(pos)
{
}

namespace {

const char UNARY_MINUS = '~';

int precedence(char op)
{
    switch (op) {
    case '+':
    case '-':
        return 1;
    case '*':
    case '/':
        return 2;
    case UNARY_MINUS:
        return 3;
    default:
        return 0;
    }
}

void apply(Stack<Expr*>& operands, char op, std::size_t pos)
{
    if (op == UNARY_MINUS) {
        if (operands.empty())
            throw ParseError("missing operand", pos);
        operands.push(new Negate(operands.pop()));
        return;
    }
    if (operands.size() < 2)
        throw ParseError("missing operand", pos);
    Expr* rhs = operands.pop();
    Expr* lhs = operands.pop();
    operands.push(new Binary(op, lhs, rhs));
}

void delete_all(Stack<Expr*>& operands)
{
    while (!operands.empty())
        delete operands.pop();
}

} // namespace

Expr* parse(const std::string& text, const Rational& ans)
{
    Lexer lexer(text);
    Stack<Expr*> operands;
    Stack<char> operators;
    bool expect_operand = true;
    try {
        for (;;) {
            Token token = lexer.next();
            if (token.kind == Token::END)
                break;
            if (expect_operand) {
                if (token.kind == Token::NUMBER) {
                    Rational value;
                    if (!Rational::parse(token.text, value))
                        throw ParseError("bad number", token.pos);
                    operands.push(new Number(value));
                    expect_operand = false;
                } else if (token.kind == Token::NAME) {
                    if (token.text != "ans")
                        throw ParseError("unknown name '" + token.text + "'", token.pos);
                    operands.push(new Number(ans));
                    expect_operand = false;
                } else if (token.kind == Token::LPAREN) {
                    operators.push('(');
                } else if (token.kind == Token::OPERATOR && token.text == "-") {
                    operators.push(UNARY_MINUS);
                } else {
                    throw ParseError("expected a number", token.pos);
                }
            } else if (token.kind == Token::OPERATOR) {
                char op = token.text[0];
                while (!operators.empty() && precedence(operators.top()) >= precedence(op))
                    apply(operands, operators.pop(), token.pos);
                operators.push(op);
                expect_operand = true;
            } else if (token.kind == Token::RPAREN) {
                while (!operators.empty() && operators.top() != '(')
                    apply(operands, operators.pop(), token.pos);
                if (operators.empty())
                    throw ParseError("unbalanced ')'", token.pos);
                operators.pop();
            } else {
                throw ParseError("expected an operator", token.pos);
            }
        }
        if (expect_operand)
            throw ParseError("unexpected end of input", text.size());
        while (!operators.empty()) {
            char op = operators.pop();
            if (op == '(')
                throw ParseError("unbalanced '('", text.size());
            apply(operands, op, text.size());
        }
    } catch (...) {
        delete_all(operands);
        throw;
    }
    return operands.pop();
}

} // namespace calc
